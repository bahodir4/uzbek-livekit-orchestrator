"""LiveKit TTS adapter for the existing authenticated dual-TTS HTTP PCM gateway.

The gateway streams signed little-endian 24-kHz mono PCM16. LiveKit's text
StreamAdapter handles incremental sentence formation; audio is pushed to
AudioEmitter as each upstream chunk arrives, without waiting for a WAV file.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

import aiohttp
from livekit.agents import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS, tts

log = logging.getLogger(__name__)
MODELS = frozenset(("sayro", "navoiy"))
SAMPLE_RATE = 24000


class DualGatewayTTS(tts.TTS):
    def __init__(self, *, url: str, api_key: str, model: str = "sayro",
                 emotion: str = "calm", voice: str = "sayro",
                 timeout_seconds: float = 120, max_audio_seconds: float = 35,
                 max_chars_per_request: int = 180):
        if model not in MODELS:
            raise ValueError("Unsupported model: use sayro or navoiy")
        if not (32 <= max_chars_per_request <= 350):
            raise ValueError("max_chars_per_request must be between 32 and 350")
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False),
                         sample_rate=SAMPLE_RATE, num_channels=1)
        self.url = url.rstrip("/") + "/v1/tts/stream"
        self.api_key = api_key
        self.selected_model = model
        self.emotion = emotion
        self.voice = voice
        self.timeout_seconds = timeout_seconds
        self.max_audio_seconds = max_audio_seconds
        self.max_chars_per_request = max_chars_per_request
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    @property
    def model(self) -> str:
        return self.selected_model

    @property
    def provider(self) -> str:
        return "uzbek-dual-tts-gateway"

    async def _get_client(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
                    connector=aiohttp.TCPConnector(limit=8, keepalive_timeout=30),
                )
            return self._session

    def synthesize(self, text: str, *,
                   conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> tts.ChunkedStream:
        return _PCMStream(tts_instance=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        async with self._lock:
            if self._session is not None and not self._session.closed:
                await self._session.close()
        await super().aclose()


class _PCMStream(tts.ChunkedStream):
    def __init__(self, *, tts_instance: DualGatewayTTS, input_text: str,
                 conn_options: APIConnectOptions):
        super().__init__(tts=tts_instance, input_text=input_text, conn_options=conn_options)
        self.gateway = tts_instance
        self.text = input_text

    async def _run(self, emitter: tts.AudioEmitter) -> None:
        from ..audio_utils import split_long_phrase
        gateway = self.gateway
        # Snapshot the model before any async IO: model changes cannot reroute an active stream.
        model = gateway.selected_model
        emitter.initialize(request_id=uuid.uuid4().hex, sample_rate=SAMPLE_RATE,
                           num_channels=1, mime_type="audio/pcm", frame_size_ms=40)
        session = await gateway._get_client()
        total = 0
        limit = int(gateway.max_audio_seconds * SAMPLE_RATE * 2)
        for phrase in split_long_phrase(self.text, gateway.max_chars_per_request):
            payload = {"model": model, "text": phrase, "emotion": gateway.emotion,
                       "voice": gateway.voice}
            headers = {"Content-Type": "application/json", "Accept": "application/octet-stream",
                       "Authorization": f"Bearer {gateway.api_key}"}
            remainder = b""
            async with session.post(gateway.url, json=payload, headers=headers) as response:
                response.raise_for_status()
                if response.headers.get("X-Audio-Sample-Rate") != str(SAMPLE_RATE):
                    raise ValueError("TTS gateway sample rate mismatch")
                if response.headers.get("X-Audio-Channels") != "1":
                    raise ValueError("TTS gateway channel mismatch")
                if response.headers.get("X-Audio-Format") != "pcm_s16le":
                    raise ValueError("TTS gateway audio format mismatch")
                if response.content_type != "application/octet-stream":
                    raise ValueError("TTS gateway must return raw PCM, not WAV/JSON/SSE")
                async for chunk in response.content.iter_chunked(3840):
                    remainder += chunk
                    n = len(remainder) & ~1  # 16-bit samples; HTTP can split mid-sample
                    if n:
                        total += n
                        if total > limit:
                            raise ValueError("TTS output exceeded maximum audio duration")
                        emitter.push(remainder[:n])
                        remainder = remainder[n:]
                if remainder:
                    raise ValueError("TTS gateway returned incomplete PCM16 sample")
        if not total:
            raise ValueError("TTS gateway returned empty audio")
        log.debug("TTS streamed model=%s audio_sec=%.3f", model, total / (2 * SAMPLE_RATE))
