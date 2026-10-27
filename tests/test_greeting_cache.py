"""The greeting is rendered once, stored, and replayed from the cache; a bad or missing cache never breaks the call."""
import asyncio

import pytest

from voice_agent import greeting_cache as gc
from voice_agent.adapters.dual_tts import DualGatewayTTS

TEXT = "Assalomu alaykum! Men Sofiya, kredit bo'yicha maslahatchiman. Qanday yordam bera olaman?"


@pytest.fixture(autouse=True)
def cache_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("GREETING_CACHE_DIR", str(tmp_path / "gc"))


def gateway(monkeypatch, calls):
    t = DualGatewayTTS(url="http://x", api_key="k", model="navoiy", voice="navoiy")

    async def fake_chunks(payload):
        calls.append(payload["text"])
        yield b"\x01\x00" * 4800
        yield b"\x02\x00" * 4800
    monkeypatch.setattr(t, "pcm_chunks", fake_chunks)
    return t


def test_key_depends_on_voice_model_and_text():
    a = gc.cache_path("navoiy", "navoiy", "calm", TEXT)
    assert a == gc.cache_path("navoiy", "navoiy", "calm", TEXT + " ")        # surrounding space does not matter
    assert a != gc.cache_path("sayro", "navoiy", "calm", TEXT)
    assert a != gc.cache_path("navoiy", "navoiy", "warm", TEXT)
    assert a != gc.cache_path("navoiy", "navoiy", "calm", TEXT + "!")


def test_fill_renders_once_and_then_serves_from_disk(monkeypatch):
    calls = []
    g = gateway(monkeypatch, calls)
    first = asyncio.run(gc.fill(g, TEXT))
    assert first and len(first) == 4 * 4800 and calls                       # rendered from the gateway
    made = len(calls)
    second = asyncio.run(gc.fill(g, TEXT))
    assert second == first and len(calls) == made                           # no second request
    assert gc.load(gc.cache_path("navoiy", "navoiy", "calm", TEXT)) == first


def test_a_failing_gateway_returns_none_and_caches_nothing(monkeypatch):
    t = DualGatewayTTS(url="http://x", api_key="k", model="navoiy", voice="navoiy")

    async def broken(payload):
        raise RuntimeError("pod down")
        yield b""
    monkeypatch.setattr(t, "pcm_chunks", broken)
    assert asyncio.run(gc.fill(t, TEXT)) is None
    assert gc.load(gc.cache_path("navoiy", "navoiy", "calm", TEXT)) is None


def test_corrupt_files_are_ignored(tmp_path):
    p = gc.cache_path("navoiy", "navoiy", "calm", TEXT)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"")                                                      # empty
    assert gc.load(p) is None
    p.write_bytes(b"\x01\x00\x02")                                          # odd length: half a sample
    assert gc.load(p) is None


def test_frames_are_20ms_and_cover_all_audio():
    async def go():
        return [f async for f in gc.frames(b"\x05\x00" * 1000)]
    out = asyncio.run(go())
    assert all(f.samples_per_channel == 480 and f.sample_rate == 24000 and f.num_channels == 1 for f in out)
    assert len(out) == 3                                                    # 1000 samples -> 480 + 480 + 40 (padded)
