#!/usr/bin/env python3
"""Measure each stage of a voice turn against the pods named in config/agent.yaml.

    python scripts/bench_latency.py            # 5 rounds per stage
    python scripts/bench_latency.py -n 10 --text "Kredit olish uchun nima kerak?"

Stages (median / min / max, seconds):
  proxy   trivial GET per pod on a reused connection (shows proxy / network cost per request)
  llm     streaming chat completion: time to first token and total
  tts     time to first audio for a short opener and a sentence, HTTP vs persistent WebSocket
  stt     time from the end of spoken audio to the final transcript (WebSocket stream)
The last line adds up the parts of a spoken turn that the agent waits for in sequence.
Reads .env for the API keys. Run it before and after a redeploy to see what each change bought.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import aiohttp
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def fmt(values: list[float]) -> str:
    if not values:
        return "no data"
    return f"{statistics.median(values):5.2f}   min {min(values):5.2f}   max {max(values):5.2f}"


def row(label: str, values: list[float]) -> None:
    print(f"  {label:<44} {fmt(values)}")


async def bench_proxy(session, specs, n):
    print("\nproxy / network (trivial request, reused connection)")
    targets = {
        "stt  GET /health/ready": (specs["stt"]["url"].rstrip("/") + "/health/ready", {"Authorization": f"Bearer {specs['stt_key']}"}),
        "llm  GET /models": (specs["llm"]["url"].rstrip("/") + "/models", {"Authorization": f"Bearer {specs['llm_key']}"}),
        "tts  GET /health/live": (specs["tts"]["url"].rstrip("/") + "/health/live", {}),
    }
    out = {}
    for label, (url, headers) in targets.items():
        times = []
        for _ in range(n + 1):
            t0 = time.perf_counter()
            try:
                async with session.get(url, headers=headers) as r:
                    await r.read()
                    if r.status != 200:
                        raise RuntimeError(f"HTTP {r.status}")
            except Exception as exc:
                print(f"  {label:<44} FAILED ({exc})")
                times = []
                break
            times.append(time.perf_counter() - t0)
        times = times[1:]  # first request also pays the TLS handshake
        if times:
            row(label, times)
        out[label] = times
    return out


async def bench_llm(session, specs, cfg, n):
    print("\nllm (streaming)")
    spec = specs["llm"]
    system = (ROOT / cfg["agent"]["instructions_file"]).read_text(encoding="utf-8")
    body = {"model": spec["model"], "stream": True, "temperature": spec.get("temperature", 0.35),
            "max_tokens": int(spec.get("max_completion_tokens", 120)),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": "Salom, kredit olish uchun qanday hujjatlar kerak?"}]}
    ttft, total = [], []
    for _ in range(n + 1):
        t0 = time.perf_counter()
        first = None
        try:
            async with session.post(spec["url"].rstrip("/") + "/chat/completions", json=body,
                                    headers={"Authorization": f"Bearer {specs['llm_key']}"}) as r:
                r.raise_for_status()
                async for line in r.content:
                    if line.startswith(b"data: ") and b'"content":"' in line and first is None:
                        first = time.perf_counter() - t0
        except Exception as exc:
            print(f"  FAILED ({exc})")
            return None
        ttft.append(first or 0.0)
        total.append(time.perf_counter() - t0)
    row("time to first token", ttft[1:])
    row("total reply", total[1:])
    return ttft[1:]


async def bench_tts(specs, cfg, n, text):
    from voice_agent.adapters.dual_tts import DualGatewayTTS
    print("\ntts (time to first audio)")
    spec = specs["tts"]
    results = {}
    for transport in ("http", "ws"):
        t = DualGatewayTTS(url=spec["url"], api_key=specs["tts_key"], model=spec.get("model", "navoiy"),
                           voice=spec.get("voice", "navoiy"), transport=transport)
        try:
            t.prewarm()
            await asyncio.sleep(2.5 if transport == "ws" else 0)  # let the websocket pool open
            for label, phrase in (("opener 'Albatta.'", "Albatta."), ("sentence", text)):
                times = []
                for i in range(n + 1):
                    payload = {"model": t.selected_model, "text": phrase, "voice": t.voice, "emotion": "calm"}
                    t0 = time.perf_counter()
                    first = None
                    try:
                        async for _ in t.pcm_chunks(payload):
                            first = first or time.perf_counter() - t0
                    except Exception as exc:
                        print(f"  {transport} {label}: FAILED ({exc})")
                        break
                    times.append(first or 0.0)
                    await asyncio.sleep(0.3)
                if times[1:]:
                    row(f"{transport:<4} {label}", times[1:])
                    results[(transport, label)] = times[1:]
        finally:
            await t.aclose()
    return results


async def synth_pcm(specs, text) -> bytes:
    from voice_agent.adapters.dual_tts import DualGatewayTTS
    spec = specs["tts"]
    t = DualGatewayTTS(url=spec["url"], api_key=specs["tts_key"], model=spec.get("model", "navoiy"),
                       voice=spec.get("voice", "navoiy"))
    try:
        payload = {"model": t.selected_model, "text": text, "voice": t.voice, "emotion": "calm"}
        return b"".join([c async for c in t.pcm_chunks(payload)])
    finally:
        await t.aclose()


async def bench_stt(session, specs, n, text):
    print("\nstt (end of audio -> final transcript, realtime-paced stream)")
    try:
        pcm24 = np.frombuffer(await synth_pcm(specs, text), dtype="<i2").astype(np.float32)
    except Exception as exc:
        print(f"  skipped: could not synthesize test audio ({exc})")
        return None
    pcm16 = np.interp(np.linspace(0, len(pcm24) - 1, int(len(pcm24) * 16000 / 24000)),
                      np.arange(len(pcm24)), pcm24).astype("<i2").tobytes()
    url = specs["stt"]["url"].rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + "/v1/stt/stream"
    connects, finals = [], []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            async with session.ws_connect(url, headers={"Authorization": f"Bearer {specs['stt_key']}"}, heartbeat=20) as ws:
                await ws.send_json({"type": "config", "sample_rate": 16000, "channels": 1})
                ready = await ws.receive_json()
                connects.append(time.perf_counter() - t0)
                end_sent = None
                silence = b"\x00" * (640 * 2)
                done = asyncio.Event()
                async def sender():
                    nonlocal end_sent
                    step = 640 * 2
                    for i in range(0, len(pcm16), step):
                        await ws.send_bytes(pcm16[i:i + step])
                        await asyncio.sleep(0.02)
                    end_sent = time.perf_counter()
                    # Real trailing silence, paced like a live call, so the server detects the end of speech
                    # itself (no "end" message). The measurement starts when the speech stops.
                    for _ in range(200):
                        if done.is_set():
                            return
                        await ws.send_bytes(silence)
                        await asyncio.sleep(0.02)
                    await ws.send_json({"type": "end"})
                task = asyncio.create_task(sender())
                async with asyncio.timeout(20):
                    async for m in ws:
                        if m.type == aiohttp.WSMsgType.TEXT:
                            ev = json.loads(m.data)
                            if ev.get("type") == "final_transcript" and end_sent:
                                finals.append(time.perf_counter() - end_sent)
                                done.set()
                                break
                task.cancel()
        except Exception as exc:
            print(f"  FAILED ({exc})")
            break
    if connects:
        row("websocket connect + ready", connects)
    if finals:
        row("end of audio -> final transcript", finals)
    return finals


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5, help="rounds per stage")
    ap.add_argument("--text", default="Kredit olish uchun sizga pasport va daromad ma'lumotnomasi kerak bo'ladi.")
    args = ap.parse_args()
    load_dotenv()
    from voice_agent.configuration import load, active_spec, credential
    cfg = load()
    specs = {k: active_spec(cfg, k) for k in ("stt", "llm", "tts")}
    specs.update(stt_key=credential(specs["stt"], required=True), llm_key=credential(specs["llm"]) or "EMPTY",
                 tts_key=credential(specs["tts"], required=True))
    print(f"urls: stt={specs['stt']['url']}  llm={specs['llm']['url']}  tts={specs['tts']['url']}")
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await bench_proxy(session, specs, args.n)
        ttft = await bench_llm(session, specs, cfg, args.n)
        tts = await bench_tts(specs, cfg, args.n, args.text)
        stt = await bench_stt(session, specs, args.n, args.text)
    best = {}
    for (transport, label), v in (tts or {}).items():
        if label.startswith("opener"):
            best[transport] = statistics.median(v)
    if ttft and stt and best:
        base = statistics.median(stt) + statistics.median(ttft)
        print("\nestimated speech end -> first agent audio (stt final + llm first token + tts opener):")
        for transport, v in best.items():
            print(f"  with {transport:<4} tts: {base + v:5.2f} s")


if __name__ == "__main__":
    asyncio.run(main())
