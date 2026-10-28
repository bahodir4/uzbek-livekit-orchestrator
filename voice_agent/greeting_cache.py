"""Pre-rendered agent greetings.

Every agent opens a call with the same fixed sentence. Synthesizing it live costs 2.5-6 s of silence at the start of
the call (cold connection plus the proxy plus the model). The audio is rendered once with the real voice, stored on the
data volume and played from memory the moment the caller joins. A missing cache entry is never an error: the greeting
is then synthesized live as before and stored for the next call.

    python -m voice_agent.greeting_cache        # render the greetings of all agents (run once after a deploy)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import AsyncIterator

log = logging.getLogger(__name__)
SAMPLE_RATE = 24000
FRAME_SAMPLES = SAMPLE_RATE // 50          # 20 ms frames


def cache_dir() -> Path:
    configured = os.getenv("GREETING_CACHE_DIR")
    if configured:
        return Path(configured)
    data = Path("/app/data")
    base = data if data.is_dir() and os.access(data, os.W_OK) else Path(tempfile.gettempdir())
    return base / "greeting-cache"


def cache_path(model: str, voice: str, emotion: str, text: str) -> Path:
    key = hashlib.sha256("\x1f".join((model, voice, emotion, text.strip())).encode("utf-8")).hexdigest()[:24]
    return cache_dir() / f"{key}.pcm"


def cached_greeting(config: dict, persona: str, model: str) -> bytes | None:
    """The stored greeting of an agent for the browser, or None (not rendered yet, auto_greet off, other TTS type).

    The key must equal the one the worker builds from its DualGatewayTTS (providers.build_tts)."""
    from .configuration import active_spec
    from .experience import PERSONAS, canonical_persona
    if not config.get("agent", {}).get("auto_greet", True):
        return None
    spec = active_spec(config, "tts")
    if spec["type"] != "dual_gateway_stream" or model not in ("sayro", "navoiy"):
        return None
    voice = spec.get("voice", model) if model == spec.get("model", "sayro") else model
    emotion = spec.get("greeting_emotion") or spec.get("emotion", "calm")
    return load(cache_path(model, voice, emotion, PERSONAS[canonical_persona(persona)]["greeting"]))


def load(path: Path) -> bytes | None:
    try:
        pcm = path.read_bytes()
    except OSError:
        return None
    return pcm if pcm and len(pcm) % 2 == 0 else None     # an empty or half-written file is ignored


MAX_ENTRIES = 3000


def _prune(directory: Path) -> None:
    """Keep the cache bounded: when it grows past MAX_ENTRIES, drop the least recently written files."""
    try:
        files = sorted(directory.glob("*.pcm"), key=lambda f: f.stat().st_mtime)
        for old in files[: max(0, len(files) - MAX_ENTRIES + 300)]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def store(path: Path, pcm: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if hash(path.name) % 40 == 0:
            _prune(path.parent)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(pcm)
        tmp.replace(path)                                 # atomic: a reader never sees a partial file
    except OSError as exc:
        log.warning("could not store the greeting cache: %s", exc)


async def render(gateway, text: str, emotion: str | None = None) -> bytes:
    """Synthesize ``text`` with the gateway's real voice and return raw PCM16."""
    from .audio_utils import split_long_phrase
    out = bytearray()
    for phrase in split_long_phrase(text.strip(), gateway.max_chars_per_request):
        payload = {"model": gateway.selected_model, "text": phrase, "emotion": emotion or gateway.emotion,
                   "voice": gateway.voice}
        async for chunk in gateway.pcm_chunks(payload):
            out += chunk
    return bytes(out[: len(out) & ~1])


async def frames(pcm: bytes) -> AsyncIterator:
    """20 ms audio frames for ``AgentSession.say(text, audio=...)``."""
    from livekit import rtc
    step = FRAME_SAMPLES * 2
    for i in range(0, len(pcm), step):
        piece = pcm[i:i + step]
        piece += b"\x00" * (step - len(piece))
        yield rtc.AudioFrame(data=piece, sample_rate=SAMPLE_RATE, num_channels=1, samples_per_channel=FRAME_SAMPLES)


async def fill(gateway, text: str, emotion: str | None = None) -> bytes | None:
    """Render and store a phrase if it is not cached yet. Never raises."""
    path = cache_path(gateway.selected_model, gateway.voice, emotion or gateway.emotion, text)
    cached = load(path)
    if cached:
        return cached
    try:
        pcm = await render(gateway, text, emotion)
    except Exception as exc:
        log.warning("greeting render failed: %s", exc)
        return None
    if pcm:
        store(path, pcm)
        log.info("greeting cached (%.1fs of audio): %s", len(pcm) / (2 * SAMPLE_RATE), path.name)
    return pcm or None


async def render_all() -> int:
    """Greetings (with the greeting emotion) and the fixed phrases of agents/phrases.yaml (with the normal emotion)."""
    from . import agent_registry
    from .configuration import load as load_config
    from .experience import PERSONAS
    from .providers import build_tts
    gateway = build_tts(load_config())
    gateway = getattr(gateway, "_wrapped_tts", gateway)
    done = failed = 0
    try:
        jobs = [(f"greeting {aid}", p["greeting"], gateway.greeting_emotion) for aid, p in PERSONAS.items()]
        for kind, texts in agent_registry.get().phrases.items():
            jobs += [(kind, t, None) for t in texts]
        for label, text, emotion in jobs:
            pcm = await fill(gateway, text, emotion)
            print(f"{label:<18} {'ok  %4.1fs  ' % (len(pcm) / (2 * SAMPLE_RATE)) if pcm else 'FAILED       '}{text[:60]}")
            done += bool(pcm)
            failed += not pcm
    finally:
        await gateway.aclose()
    return done if not failed else 0


if __name__ == "__main__":
    n = asyncio.run(render_all())
    raise SystemExit(0 if n else 1)
