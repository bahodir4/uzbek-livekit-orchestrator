"""DualGatewayTTS transports against a tiny fake of the gateway's HTTP and WebSocket protocol."""
import asyncio
import json

import pytest
from aiohttp import WSMsgType, web

from voice_agent.adapters.dual_tts import DualGatewayTTS, SAMPLE_RATE

PCM = b"\x01\x00" * 4800  # 0.2 s


async def http_stream(request):
    body = await request.json()
    resp = web.StreamResponse(headers={"Content-Type": "application/octet-stream", "X-Audio-Format": "pcm_s16le",
                                       "X-Audio-Sample-Rate": str(SAMPLE_RATE), "X-Audio-Channels": "1"})
    await resp.prepare(request)
    request.app["http_texts"].append(body["text"])
    for i in range(0, len(PCM), 3840):
        await resp.write(PCM[i:i + 3840])
    return resp


async def ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    request.app["ws_auth"].append(request.headers.get("Authorization"))
    await ws.send_json({"type": "ready", "format": "pcm_s16le", "sample_rate": SAMPLE_RATE})
    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        req = json.loads(msg.data)
        if req.get("type") != "synthesize":
            continue
        request.app["ws_texts"].append(req["text"])
        if req["text"] == "boom":
            await ws.send_json({"type": "error", "request_id": req["request_id"], "detail": "upstream down"})
            continue
        await ws.send_json({"type": "start", "request_id": req["request_id"], "format": "pcm_s16le",
                            "sample_rate": SAMPLE_RATE, "channels": 1, "ttfa_ms": 1})
        for i in range(0, len(PCM), 3000):
            await ws.send_bytes(PCM[i:i + 3000])
        await ws.send_json({"type": "end", "request_id": req["request_id"], "total_ms": 2})
    return ws


@pytest.fixture
def gateway():
    async def start():
        app = web.Application()
        app["http_texts"], app["ws_texts"], app["ws_auth"] = [], [], []
        app.add_routes([web.post("/v1/tts/stream", http_stream), web.get("/v1/tts/ws", ws_handler)])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        return app, runner, f"http://127.0.0.1:{port}"
    return start


async def collect(t, text):
    return b"".join([c async for c in t.pcm_chunks({"model": "navoiy", "text": text, "voice": "navoiy", "emotion": "calm"})])


def test_ws_reuses_one_connection_and_prewarms(gateway):
    async def run():
        app, runner, url = await gateway()
        t = DualGatewayTTS(url=url, api_key="k", model="navoiy", voice="navoiy", transport="ws")
        try:
            t.prewarm()
            await asyncio.sleep(0.3)
            assert len(t._ws_idle) == 2 and len(app["ws_auth"]) == 2       # pool opened before any text
            assert app["ws_auth"][0] == "Bearer k"
            assert await collect(t, "Salom.") == PCM
            assert await collect(t, "Yana bir gap.") == PCM
            assert app["ws_texts"] == ["Salom.", "Yana bir gap."]
            assert len(app["ws_auth"]) == 2                                 # no new connections were needed
            assert app["http_texts"] == []
        finally:
            await t.aclose()
            await runner.cleanup()
    asyncio.run(run())


def test_ws_error_drops_connection_and_raises(gateway):
    async def run():
        app, runner, url = await gateway()
        t = DualGatewayTTS(url=url, api_key="k", model="navoiy", voice="navoiy", transport="ws")
        try:
            with pytest.raises(RuntimeError, match="upstream down"):
                await collect(t, "boom")
            assert t._ws_idle == []                                         # a failed request's socket is not reused
            assert await collect(t, "Salom.") == PCM                        # next phrase opens a fresh connection
        finally:
            await t.aclose()
            await runner.cleanup()
    asyncio.run(run())


def test_ws_unavailable_falls_back_to_http(gateway):
    async def run():
        app, runner, url = await gateway()
        t = DualGatewayTTS(url=url, api_key="k", model="navoiy", voice="navoiy", transport="ws")
        t.ws_url = url.replace("http://", "ws://") + "/does-not-exist"       # handshake fails
        try:
            assert await collect(t, "Salom.") == PCM
            assert app["http_texts"] == ["Salom."] and t._ws_down_until > 0
            assert await collect(t, "Yana.") == PCM                         # stays on HTTP during the cool-down
            assert app["http_texts"] == ["Salom.", "Yana."]
        finally:
            await t.aclose()
            await runner.cleanup()
    asyncio.run(run())


def test_http_transport_unchanged(gateway):
    async def run():
        app, runner, url = await gateway()
        t = DualGatewayTTS(url=url, api_key="k", model="navoiy", voice="navoiy")
        try:
            t.prewarm()                                                     # no-op for http
            assert await collect(t, "Salom.") == PCM
            assert app["ws_auth"] == [] and app["http_texts"] == ["Salom."]
        finally:
            await t.aclose()
            await runner.cleanup()
    asyncio.run(run())
