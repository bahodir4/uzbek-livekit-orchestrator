"""Streaming TTS input: pieces are requested ahead of playback (no request round trip between pieces) but played in order."""
import asyncio
import time

import pytest

from voice_agent.adapters.dual_tts import DualGatewayTTS

S1 = "Birinchi gap hozir tugaydi va bu yerda yetarlicha uzun matn bor. "
S2 = "Ikkinchi gap ham uzun bo'lsin, shunda u alohida bo'lak bo'ladi va hech kimni kutmaydi. "
S3 = "Uchinchi gap ham xuddi shunday uzun va oxirida to'xtash belgisi bilan tugaydi albatta."


def make_tts(monkeypatch, *, prefetch, delay=0.3, fail_on=None, starts=None):
    t = DualGatewayTTS(url="http://x", api_key="k", model="navoiy", voice="navoiy",
                       max_chars_per_request=340, prefetch_phrases=prefetch, phrase_cache=False)

    async def fake_chunks(payload):
        phrase = payload["text"]
        if starts is not None:
            starts.append((phrase, time.perf_counter()))
        await asyncio.sleep(delay)                       # time to first audio
        if fail_on and fail_on in phrase:
            raise RuntimeError("boom")
        yield bytes([len(phrase) % 200]) * 960

    monkeypatch.setattr(t, "pcm_chunks", fake_chunks)
    return t


async def run(t, pieces):
    stream = t.stream()
    for piece in pieces:                                 # LLM tokens arrive in small pieces
        stream.push_text(piece)
    stream.end_input()
    out = []
    async for ev in stream:
        out.append(bytes(ev.frame.data))
    return b"".join(out)


def test_streaming_is_enabled_and_pieces_overlap(monkeypatch):
    text = S1 + S2 + S3
    pieces = [text[i:i + 9] for i in range(0, len(text), 9)]

    async def go(prefetch):
        starts = []
        t = make_tts(monkeypatch, prefetch=prefetch, starts=starts)
        t0 = time.perf_counter()
        audio = await run(t, pieces)
        return time.perf_counter() - t0, audio, starts

    assert DualGatewayTTS(url="http://x", api_key="k", model="navoiy").capabilities.streaming is True
    serial_time, serial_audio, serial_starts = asyncio.run(go(1))
    fast_time, fast_audio, fast_starts = asyncio.run(go(3))
    assert len(fast_starts) == 3 and [p for p, _ in fast_starts] == [p for p, _ in serial_starts]
    assert fast_audio == serial_audio and len(fast_audio) > 0       # same audio, same order
    assert fast_time < serial_time - 0.3                            # pieces requested together finish sooner


def test_error_in_a_later_piece_is_raised(monkeypatch):
    async def go():
        t = make_tts(monkeypatch, prefetch=2, fail_on="Ikkinchi")
        with pytest.raises(Exception):
            await run(t, [S1 + S2 + S3])
    asyncio.run(go())


def test_a_reply_that_is_one_sentence_makes_one_request(monkeypatch):
    async def go():
        starts = []
        t = make_tts(monkeypatch, prefetch=2, starts=starts)
        audio = await run(t, ["Men sizga kredit oylik to'lovini hisoblashda va kerakli hujjatlar ", "ro'yxatini aniqlashda yordam bera olaman."])
        return audio, starts
    audio, starts = asyncio.run(go())
    assert len(starts) == 1 and audio


def test_tts_metrics_are_reported_for_the_stream(monkeypatch):
    """LiveKit only emits TTSMetrics (ttfb, duration, audio_duration) once the stream has marked itself started."""
    async def go():
        t = make_tts(monkeypatch, prefetch=2, delay=0.2)
        events = []
        t.on("metrics_collected", events.append)
        await run(t, [S1 + S2])
        await asyncio.sleep(0.2)
        return events
    events = asyncio.run(go())
    assert len(events) == 1
    assert events[0].ttfb >= 0.2 and events[0].audio_duration > 0 and events[0].streamed is True


def test_http_prewarm_sends_one_tiny_request_and_survives_failures(monkeypatch):
    async def go(fail):
        t = DualGatewayTTS(url="http://x", api_key="k", model="navoiy", voice="navoiy")
        seen = []

        async def fake_http(payload):
            seen.append(payload["text"])
            if fail:
                raise RuntimeError("proxy down")
            yield b"\x00\x00" * 480

        monkeypatch.setattr(t, "_pcm_http", fake_http)
        t.prewarm()
        await asyncio.sleep(0.1)
        return seen
    assert asyncio.run(go(False)) == ["Ha."]
    assert asyncio.run(go(True)) == ["Ha."]               # a failing warm-up raises nothing into the call


def test_a_repeated_phrase_is_served_from_the_cache_without_a_tts_request(monkeypatch, tmp_path):
    monkeypatch.setenv("GREETING_CACHE_DIR", str(tmp_path / "gc"))

    async def go():
        starts = []
        t = make_tts(monkeypatch, prefetch=2, starts=starts, delay=0.05)
        t.phrase_cache = True
        first = await run(t, ["Hozir hisoblab beraman."])
        again = await run(t, ["Hozir hisoblab beraman."])
        return first, again, starts
    first, again, starts = asyncio.run(go())
    assert first == again and len(first) > 0              # identical audio, as with a recorded prompt
    assert len(starts) == 1                                # the second time nothing was requested


def test_cache_can_be_switched_off_and_failed_phrases_are_never_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("GREETING_CACHE_DIR", str(tmp_path / "gc"))

    async def go():
        starts = []
        t = make_tts(monkeypatch, prefetch=2, starts=starts, delay=0.01)
        t.phrase_cache = False
        await run(t, ["Rahmat, yaxshi kun tilayman."])
        await run(t, ["Rahmat, yaxshi kun tilayman."])
        failing = make_tts(monkeypatch, prefetch=2, fail_on="buzilgan", delay=0.01)
        failing.phrase_cache = True
        with pytest.raises(Exception):
            await run(failing, ["Bu buzilgan gap."])
        return starts, list((tmp_path / "gc").glob("*.pcm")) if (tmp_path / "gc").exists() else []
    starts, files = asyncio.run(go())
    assert len(starts) == 2 and files == []


def test_prompt_uses_the_fillers_from_phrases_yaml():
    from voice_agent import agent_registry
    reg = agent_registry.load()
    fillers = reg.phrases["fillers"]
    assert fillers and all(f'"{f}"' in reg.agents["sofiya"].instructions for f in fillers)
    assert "{fillers}" not in reg.agents["sofiya"].instructions
