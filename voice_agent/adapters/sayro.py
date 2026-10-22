"""Sayro vLLM-Omni HTTP WAV TTS for LiveKit sentence streaming.

One HTTP call yields a complete WAV. Wrap SayroTTS in LiveKit's StreamAdapter
upstream to buffer LLM text into sentences. This is phrase-level streaming,
not native incremental audio-token streaming.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

import aiohttp
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, tts

from ..audio_utils import decode_wav_pcm16, split_long_phrase, trim_pcm_edges

log = logging.getLogger(__name__)


class SayroTTS(tts.TTS):
    def __init__(
        self,
        *,
        url: str,
        api_key: str = "",
        voice: str = "sayro",
        language: str = "Auto",
        task_type: str = "CustomVoice",
        sample_rate: int = 24000,
        max_new_tokens: int = 180,
        max_chars_per_request: int = 105,
        timeout_seconds: float = 40,
        max_audio_seconds: float = 20,
        seed: int = 123,
        trim_edge_silence: bool = True,
        silence_threshold: int = 450,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self.url = url.rstrip("/")
        if not self.url.endswith("/v1/audio/speech"):
            self.url = self.url + "/v1/audio/speech"
        self.api_key = api_key
        self.voice = voice
        self.language = language
        self.task_type = task_type
        self.max_new_tokens = max_new_tokens
        self.max_chars_per_request = max_chars_per_request
        self.timeout_seconds = timeout_seconds
        self.max_audio_seconds = max_audio_seconds
        self.seed = seed
        self.trim_edge_silence = trim_edge_silence
        self.silence_threshold = silence_threshold
        self._http: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    @property
    def model(self) -> str:
        return "sayro-tts-1.7B"

    @property
    def provider(self) -> str:
        return "self-hosted-sayro"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> tts.ChunkedStream:
        return SayroChunkedStream(tts_instance=self, text=text, conn_options=conn_options)

    async def client(self) -> aiohttp.ClientSession:
        async with self._session_lock:
            if self._http is None or self._http.closed:
                timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
                connector = aiohttp.TCPConnector(limit=4, keepalive_timeout=30)
                self._http = aiohttp.ClientSession(timeout=timeout, connector=connector)
            return self._http

    async def aclose(self) -> None:
        async with self._session_lock:
            if self._http is not None and not self._http.closed:
                await self._http.close()
        await super().aclose()



class SayroChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts_instance: SayroTTS,
        text: str,
        conn_options: APIConnectOptions,
    ):
        super().__init__(
            tts=tts_instance,
            input_text=text,
            conn_options=conn_options,
        )
        self.sayro = tts_instance
        self.text = text

    async def _run(self, emitter: tts.AudioEmitter) -> None:
        obj = self.sayro

        emitter.initialize(
            request_id=uuid.uuid4().hex,
            sample_rate=obj.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            frame_size_ms=40,
        )

        headers = {"Content-Type": "application/json"}
        if obj.api_key:
            headers["Authorization"] = f"Bearer {obj.api_key}"

        client = await obj.client()

        for phrase in split_long_phrase(
            self.text, obj.max_chars_per_request
        ):
            payload = {
                "input": phrase,
                "voice": obj.voice,
                "language": obj.language,
                "task_type": obj.task_type,
                "non_streaming_mode": True,
                "response_format": "wav",
                "max_new_tokens": obj.max_new_tokens,
                "seed": obj.seed,
            }

            start = time.perf_counter()

            async with client.post(
                obj.url,
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                wav_bytes = await response.read()
                output_tokens = response.headers.get(
                    "x-vllm-omni-output-tokens"
                )

            generation_seconds = time.perf_counter() - start

            pcm = decode_wav_pcm16(
                wav_bytes,
                expected_rate=obj.sample_rate,
            )

            raw_seconds = len(pcm) / (obj.sample_rate * 2)

            if obj.trim_edge_silence:
                pcm = trim_pcm_edges(
                    pcm,
                    sample_rate=obj.sample_rate,
                    threshold=obj.silence_threshold,
                )

            cleaned_seconds = len(pcm) / (obj.sample_rate * 2)

            log.info(
                "Sayro phrase=%r generation=%.2fs raw=%.2fs "
                "cleaned=%.2fs output_tokens=%s",
                phrase,
                generation_seconds,
                raw_seconds,
                cleaned_seconds,
                output_tokens,
            )

            if not pcm:
                raise ValueError("Sayro returned empty audio")

            if cleaned_seconds > obj.max_audio_seconds:
                raise ValueError(
                    f"Sayro generated {cleaned_seconds:.1f}s "
                    "of audio for a short phrase"
                )

            if output_tokens:
                try:
                    if int(output_tokens) >= obj.max_new_tokens:
                        log.warning(
                            "Sayro reached max_new_tokens=%d",
                            obj.max_new_tokens,
                        )
                except ValueError:
                    pass

            emitter.push(pcm)
