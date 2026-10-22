"""LiveKit Agents Python custom STT plugin against our separate GigaAM GPU server."""
from __future__ import annotations
import asyncio
import json
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from livekit import rtc
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, LanguageCode, stt, utils
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents import APIConnectOptions


class GigaAMSTT(stt.STT):
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0):
        super().__init__(capabilities=stt.STTCapabilities(
            streaming=True, interim_results=True, offline_recognize=True,
            diarization=False, aligned_transcript=False))
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout

    @property
    def model(self) -> str:
        return 'gigaam-uzbek'

    @property
    def provider(self) -> str:
        return 'self-hosted-gigaam'

    @property
    def headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self.api_key}'}

    async def _recognize_impl(self, buffer: utils.AudioBuffer, *,
                              language: NotGivenOr[str] = NOT_GIVEN,
                              conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> stt.SpeechEvent:
        wav = rtc.combine_audio_frames(buffer).to_wav_bytes()
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            data = aiohttp.FormData()
            data.add_field('file', wav, filename='utterance.wav', content_type='audio/wav')
            data.add_field('language', 'uz')
            async with client.post(self.base_url + '/v1/audio/transcriptions', data=data,
                                   headers=self.headers) as response:
                response.raise_for_status()
                payload = await response.json()
        return stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                               alternatives=[stt.SpeechData(language=LanguageCode('uz'),
                                                            text=payload['text'])])

    def stream(self, *, language: NotGivenOr[str] = NOT_GIVEN,
               conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> stt.SpeechStream:
        return GigaAMSpeechStream(stt_instance=self, conn_options=conn_options)


class GigaAMSpeechStream(stt.SpeechStream):
    def __init__(self, *, stt_instance: GigaAMSTT, conn_options: APIConnectOptions):
        # The base class resamples input to 16 kHz before sending it to _input_ch.
        super().__init__(stt=stt_instance, conn_options=conn_options, sample_rate=16000)
        self.client_stt = stt_instance

    async def _run(self) -> None:
        url = self.client_stt.base_url + '/v1/stt/stream'
        parts = urlsplit(url)
        scheme = {'https': 'wss', 'http': 'ws'}.get(parts.scheme, parts.scheme)
        ws_url = urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=self.client_stt.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.ws_connect(ws_url, headers=self.client_stt.headers, heartbeat=20,
                                         max_msg_size=1024 * 1024) as ws:
                await ws.send_json({'type': 'config', 'sample_rate': 16000, 'channels': 1})

                async def send_audio():
                    async for item in self._input_ch:
                        if isinstance(item, rtc.AudioFrame):
                            if item.num_channels != 1:
                                raise ValueError('GigaAMSTT requires mono AudioFrames')
                            await ws.send_bytes(bytes(item.data))
                        elif isinstance(item, self._FlushSentinel):
                            await ws.send_json({'type': 'flush'})
                    await ws.send_json({'type': 'end'})

                async def read_events():
                    async for message in ws:
                        if message.type != aiohttp.WSMsgType.TEXT:
                            if message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                                raise RuntimeError('STT WebSocket unexpectedly disconnected')
                            continue
                        payload = json.loads(message.data)
                        kind = payload.get('type')
                        mapping = {
                            'start_of_speech': stt.SpeechEventType.START_OF_SPEECH,
                            'interim_transcript': stt.SpeechEventType.INTERIM_TRANSCRIPT,
                            'final_transcript': stt.SpeechEventType.FINAL_TRANSCRIPT,
                            'end_of_speech': stt.SpeechEventType.END_OF_SPEECH,
                        }
                        if kind in mapping:
                            alternatives = []
                            if kind in ('interim_transcript', 'final_transcript'):
                                alternatives = [stt.SpeechData(language=LanguageCode('uz'),
                                                               text=payload.get('transcript', ''))]
                            self._event_ch.send_nowait(stt.SpeechEvent(type=mapping[kind], alternatives=alternatives))
                        elif kind == 'error':
                            raise RuntimeError(f"GigaAM server error: {payload.get('code')}")
                        elif kind == 'session.closed':
                            return
                    raise RuntimeError('STT stream disconnected without session.closed')

                sender = asyncio.create_task(send_audio())
                receiver = asyncio.create_task(read_events())
                try:
                    done, pending = await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_EXCEPTION)
                    for task in done:
                        task.result()
                    if receiver in done:
                        await sender
                    else:
                        await receiver
                finally:
                    for task in (sender, receiver):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(sender, receiver, return_exceptions=True)
