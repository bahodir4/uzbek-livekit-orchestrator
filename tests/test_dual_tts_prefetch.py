"""Phrases are requested ahead of playback (no silent gap between phrases) but emitted strictly in order."""
import asyncio
import time

import pytest

from voice_agent.adapters.dual_tts import DualGatewayTTS, SAMPLE_RATE

TEXT = "Birinchi gap shu. Ikkinchi gap bu yerda. Uchinchi gap oxirida bo'ladi."


def make_tts(monkeypatch, *, prefetch, delay=0.25, fail_on=None, log=None):
    t = DualGatewayTTS(url="http://x", api_key="k", model="navoiy", voice="navoiy",
                       max_chars_per_request=32, prefetch_phrases=prefetch, phrase_cache=False)

    async def fake_chunks(payload):
        phrase = payload["text"]
        if log is not None:
            log.append(("start", phrase, time.perf_counter()))
        await asyncio.sleep(delay)                       # time to first audio
        if fail_on and fail_on in phrase:
            raise RuntimeError("boom")
        yield bytes([len(phrase) % 200]) * 960
        if log is not None:
            log.append(("end", phrase, time.perf_counter()))

    monkeypatch.setattr(t, "pcm_chunks", fake_chunks)
    return t


async def collect(t):
    out = []
    async with t.synthesize(TEXT) as stream:
        async for ev in stream:
            out.append(bytes(ev.frame.data))
    return b"".join(out)


def test_prefetch_overlaps_generation_and_keeps_order(monkeypatch):
    async def go(prefetch):
        log = []
        t = make_tts(monkeypatch, prefetch=prefetch, log=log)
        t0 = time.perf_counter()
        audio = await collect(t)
        return time.perf_counter() - t0, audio, [e for e in log if e[0] == "start"]

    serial_time, serial_audio, _ = asyncio.run(go(1))
    fast_time, fast_audio, starts = asyncio.run(go(2))
    assert fast_audio == serial_audio and len(fast_audio) > 0     # same audio, same order
    assert fast_time < serial_time - 0.15                         # overlapping requests finish sooner
    assert len(starts) >= 3


def test_error_in_later_phrase_is_raised_and_stops_cleanly(monkeypatch):
    async def go():
        t = make_tts(monkeypatch, prefetch=2, fail_on="Ikkinchi")
        with pytest.raises(Exception):
            await collect(t)
    asyncio.run(go())


def test_prefetch_setting_is_validated():
    assert DualGatewayTTS(url="http://x", api_key="k", model="navoiy", prefetch_phrases=0).prefetch_phrases == 1
