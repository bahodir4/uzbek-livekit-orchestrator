"""LiveKit TTS adapter for the existing authenticated dual-TTS PCM gateway.

The gateway streams signed little-endian 24-kHz mono PCM16. LiveKit's text
StreamAdapter handles incremental sentence formation; audio is pushed to
AudioEmitter as each upstream chunk arrives, without waiting for a WAV file.

Two transports reach the same gateway:
  * ``http``: one POST /v1/tts/stream per phrase (default).
  * ``ws``:   persistent WebSocket(s) to /v1/tts/ws, opened at session start (prewarm) and reused
              for every phrase, so a sentence does not pay a fresh proxied HTTP request. Falls back
              to HTTP automatically when the WebSocket cannot be used.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from typing import AsyncIterator

import aiohttp
from livekit.agents import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS, tts

log = logging.getLogger(__name__)
MODELS = frozenset(("sayro", "navoiy"))
TRANSPORTS = frozenset(("http", "ws"))
SAMPLE_RATE = 24000
WS_IDLE_POOL = 2          # kept-open connections (the pod serializes requests anyway)
WS_RETRY_AFTER = 30.0     # seconds to stay on HTTP after a WebSocket failure


class DualGatewayTTS(tts.TTS):
    def __init__(self, *, url: str, api_key: str, model: str = "sayro",
                 emotion: str = "calm", voice: str = "sayro",
                 timeout_seconds: float = 120, max_audio_seconds: float = 35,
                 max_chars_per_request: int = 180, transport: str = "http"):
        if model not in MODELS:
            raise ValueError("Unsupported model: use sayro or navoiy")
        if transport not in TRANSPORTS:
            raise ValueError("transport must be 'http' or 'ws'")
        if not (32 <= max_chars_per_request <= 350):
            raise ValueError("max_chars_per_request must be between 32 and 350")
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False),
                         sample_rate=SAMPLE_RATE, num_channels=1)
        base = url.rstrip("/")
        self.url = base + "/v1/tts/stream"
        self.ws_url = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/v1/tts/ws"
        self.api_key = api_key
        self.selected_model = model
        self.emotion = emotion
        self.voice = voice
        self.timeout_seconds = timeout_seconds
        self.max_audio_seconds = max_audio_seconds
        self.max_chars_per_request = max_chars_per_request
        self.transport = transport
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._ws_idle: list[aiohttp.ClientWebSocketResponse] = []
        self._ws_down_until = 0.0
        self._closed = False

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

    # ---- WebSocket transport -------------------------------------------------------------

    async def _ws_connect(self) -> aiohttp.ClientWebSocketResponse:
        session = await self._get_client()

        async def open_ready() -> aiohttp.ClientWebSocketResponse:
            ws = await session.ws_connect(self.ws_url, headers={"Authorization": f"Bearer {self.api_key}"},
                                          heartbeat=20, max_msg_size=2_000_000)
            try:
                ready = await ws.receive_json()
                if ready.get("type") != "ready" or ready.get("sample_rate") != SAMPLE_RATE:
                    raise ConnectionError(f"unexpected gateway handshake: {str(ready)[:120]}")
            except BaseException:
                await ws.close()
                raise
            return ws

        return await asyncio.wait_for(open_ready(), timeout=10)

    async def _ws_acquire(self) -> aiohttp.ClientWebSocketResponse:
        while self._ws_idle:
            ws = self._ws_idle.pop()
            if not ws.closed:
                return ws
        return await self._ws_connect()

    def _ws_release(self, ws: aiohttp.ClientWebSocketResponse, reusable: bool) -> None:
        if reusable and not ws.closed and not self._closed and len(self._ws_idle) < WS_IDLE_POOL:
            self._ws_idle.append(ws)
        else:
            asyncio.ensure_future(ws.close())

    async def _warm_ws(self) -> None:
        try:
            for _ in range(WS_IDLE_POOL - len(self._ws_idle)):
                self._ws_idle.append(await self._ws_connect())
            log.info("TTS websocket pool warm (%d connections)", len(self._ws_idle))
        except Exception as exc:
            self._ws_down_until = time.monotonic() + WS_RETRY_AFTER
            log.warning("TTS websocket prewarm failed (%s); using HTTP", type(exc).__name__)

    def prewarm(self) -> None:
        if self.transport == "ws" and not self._closed:
            asyncio.ensure_future(self._warm_ws())

    async def _pcm_ws(self, payload: dict, ws: aiohttp.ClientWebSocketResponse) -> AsyncIterator[bytes]:
        clean = False
        try:
            await ws.send_json({"type": "synthesize", "request_id": uuid.uuid4().hex, **payload})
            while True:
                msg = await ws.receive(timeout=self.timeout_seconds)
                if msg.type == aiohttp.WSMsgType.BINARY:
                    yield msg.data
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    event = msg.json()
                    kind = event.get("type")
                    if kind == "start":
                        if (event.get("sample_rate") != SAMPLE_RATE or event.get("channels") != 1
                                or event.get("format") != "pcm_s16le"):
                            raise ValueError("TTS gateway audio format mismatch")
                    elif kind == "end":
                        clean = True
                        return
                    elif kind == "error":
                        raise RuntimeError(f"TTS gateway error: {str(event.get('detail'))[:200]}")
                    elif kind == "cancelled":
                        return
                else:
                    raise ConnectionError("TTS websocket closed unexpectedly")
        finally:
            # An unfinished request (error, runaway cut, barge-in) leaves the server busy: drop the
            # connection instead of reusing it; the next phrase opens a fresh one.
            self._ws_release(ws, reusable=clean)

    # ---- HTTP transport ------------------------------------------------------------------

    async def _pcm_http(self, payload: dict) -> AsyncIterator[bytes]:
        session = await self._get_client()
        headers = {"Content-Type": "application/json", "Accept": "application/octet-stream",
                   "Authorization": f"Bearer {self.api_key}"}
        async with session.post(self.url, json=payload, headers=headers) as response:
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
                yield chunk

    def pcm_chunks(self, payload: dict) -> AsyncIterator[bytes]:
        """Raw PCM chunks for one phrase, over the configured transport (WebSocket falls back to HTTP)."""
        return self._pcm_chunks(payload)

    async def _pcm_chunks(self, payload: dict) -> AsyncIterator[bytes]:
        if self.transport == "ws" and time.monotonic() >= self._ws_down_until:
            try:
                ws = await self._ws_acquire()
            except Exception as exc:
                self._ws_down_until = time.monotonic() + WS_RETRY_AFTER
                log.warning("TTS websocket unavailable (%s); using HTTP for %.0fs",
                            type(exc).__name__, WS_RETRY_AFTER)
            else:
                async with contextlib.aclosing(self._pcm_ws(payload, ws)) as chunks:
                    async for chunk in chunks:
                        yield chunk
                return
        async with contextlib.aclosing(self._pcm_http(payload)) as chunks:
            async for chunk in chunks:
                yield chunk

    def synthesize(self, text: str, *,
                   conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> tts.ChunkedStream:
        return _PCMStream(tts_instance=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        self._closed = True
        idle, self._ws_idle = self._ws_idle, []
        for ws in idle:
            with contextlib.suppress(Exception):
                await ws.close()
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
        from ..audio_utils import split_for_fast_start
        gateway = self.gateway
        # Snapshot the model before any async IO: model changes cannot reroute an active stream.
        model = gateway.selected_model
        emitter.initialize(request_id=uuid.uuid4().hex, sample_rate=SAMPLE_RATE,
                           num_channels=1, mime_type="audio/pcm", frame_size_ms=40)
        total = 0
        limit = int(gateway.max_audio_seconds * SAMPLE_RATE * 2)
        for phrase in split_for_fast_start(self.text, gateway.max_chars_per_request):
            payload = {"model": model, "text": phrase, "emotion": gateway.emotion,
                       "voice": gateway.voice}
            remainder = b""
            # Speech is ~15 chars/s; far more audio than the text can hold means the model
            # looped. Cut that phrase off (closing the stream aborts server generation)
            # instead of failing the whole reply.
            phrase_limit = int(max(4.0, 0.16 * len(phrase)) * SAMPLE_RATE * 2)
            phrase_bytes = 0
            runaway = False
            async with contextlib.aclosing(gateway.pcm_chunks(payload)) as chunks:
                async for chunk in chunks:
                    remainder += chunk
                    n = len(remainder) & ~1  # 16-bit samples; chunks can split mid-sample
                    if n:
                        if phrase_bytes + n > phrase_limit:
                            n = max(0, phrase_limit - phrase_bytes) & ~1
                            runaway = True
                        total += n
                        if total > limit:
                            raise ValueError("TTS output exceeded maximum audio duration")
                        if n:
                            emitter.push(remainder[:n])
                            phrase_bytes += n
                        remainder = remainder[n:]
                        if runaway:
                            log.warning("TTS runaway cut at %.1fs for %d-char phrase: %r",
                                        phrase_bytes / (2 * SAMPLE_RATE), len(phrase), phrase)
                            remainder = b""
                            break
            if remainder:
                raise ValueError("TTS gateway returned incomplete PCM16 sample")
        if not total:
            raise ValueError("TTS gateway returned empty audio")
        log.debug("TTS streamed model=%s audio_sec=%.3f", model, total / (2 * SAMPLE_RATE))
