"""Finite adapter registry. Providers are selected by config; no arbitrary imports."""
from __future__ import annotations
from typing import Any
from .configuration import active_spec, credential


def build_stt(config: dict[str, Any]):
    spec = active_spec(config, "stt")
    if spec["type"] == "gigaam_ws":
        from .adapters.gigaam import GigaAMSTT
        return GigaAMSTT(
            base_url=spec["url"], api_key=credential(spec, required=True),
            timeout=float(spec.get("timeout_seconds", 30)),
        )
    raise ValueError("Unsupported STT type")


def build_llm(config: dict[str, Any]):
    spec = active_spec(config, "llm")
    if spec["type"] == "openai_compatible":
        from livekit.plugins import openai
        return openai.LLM(
            model=spec["model"], base_url=spec["url"],
            api_key=credential(spec) or "EMPTY",
            temperature=float(spec.get("temperature", 0.35)),
            max_completion_tokens=int(spec.get("max_completion_tokens", 256)),
        )
    raise ValueError("Unsupported LLM type")


def build_tts(config: dict[str, Any], *, model_override: str | None = None):
    spec = active_spec(config, "tts")
    if spec["type"] == "dual_gateway_stream":
        from livekit.agents import tts, tokenize
        from .adapters.dual_tts import DualGatewayTTS
        model = model_override or spec.get("model", "sayro")
        provider = DualGatewayTTS(
            url=spec["url"], api_key=credential(spec, required=True), model=model,
            emotion=spec.get("emotion", "calm"), voice=spec.get("voice", "sayro"),
            timeout_seconds=float(spec.get("timeout_seconds", 120)),
            max_audio_seconds=float(spec.get("max_audio_seconds", 35)),
            max_chars_per_request=int(spec.get("max_chars_per_request", 180)),
        )
        return tts.StreamAdapter(
            tts=provider,
            sentence_tokenizer=tokenize.blingfire.SentenceTokenizer(retain_format=True),
        )
    if model_override is not None:
        raise ValueError("Per-room model overrides require dual_gateway_stream")
    if spec["type"] == "sayro_http":
        from livekit.agents import tts, tokenize
        from .adapters.sayro import SayroTTS
        provider = SayroTTS(
            url=spec["url"], api_key=credential(spec), voice=spec.get("voice", "sayro"),
            language=spec.get("language", "Auto"), task_type=spec.get("task_type", "CustomVoice"),
            sample_rate=int(spec.get("sample_rate", 24000)),
            max_new_tokens=int(spec.get("max_new_tokens", 180)),
            max_chars_per_request=int(spec.get("max_chars_per_request", 115)),
            timeout_seconds=float(spec.get("timeout_seconds", 25)),
            max_audio_seconds=float(spec.get("max_audio_seconds", 20)),
            seed=int(spec.get("seed", 123)),
            trim_edge_silence=bool(spec.get("trim_edge_silence", True)),
            silence_threshold=int(spec.get("silence_threshold", 450)),
        )
        # This is the officially supported path for incremental LLM text with
        # an HTTP TTS that only produces a WAV after each phrase finishes.
        return tts.StreamAdapter(tts=provider,
                                 sentence_tokenizer=tokenize.blingfire.SentenceTokenizer(retain_format=True))
    raise ValueError("Unsupported TTS type")
