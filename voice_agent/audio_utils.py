"""Audio and text helpers independent of the LiveKit SDK."""
from __future__ import annotations

import io
import re
import wave

import numpy as np


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_CLAUSE_END = re.compile(r"(?<=[,;:])\s+")


def split_long_phrase(text: str, max_chars: int) -> list[str]:
    """Prefer sentence/clause boundaries and fall back to word boundaries.

    Does not invent punctuation or change words. LiveKit StreamAdapter normally
    passes a sentence already; this handles unusually long sentences.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    text = " ".join(text.split())
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    def pack_words(part: str) -> list[str]:
        chunks: list[str] = []
        current = ""
        for word in part.split():
            if len(word) > max_chars:
                raise ValueError("TTS input contains a token longer than max_chars")
            candidate = f"{current} {word}" if current else word
            if len(candidate) > max_chars:
                chunks.append(current)
                current = word
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    # Sentence boundaries are preferred, but short adjacent sentences are
    # grouped to avoid too many expensive HTTP calls.
    parts: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        if len(sentence) <= max_chars:
            parts.append(sentence)
        else:
            for clause in _CLAUSE_END.split(sentence):
                if len(clause) <= max_chars:
                    parts.append(clause)
                else:
                    parts.extend(pack_words(clause))

    chunks: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current} {part}" if current else part
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = part
    if current:
        chunks.append(current)
    return chunks


def trim_pcm_edges(raw: bytes, *, threshold: int = 450, sample_rate: int = 24000) -> bytes:
    """Trim leading/trailing low-amplitude frames; preserve natural pauses.

    This is NOT VAD and cannot remove hallucinated speech or codec noise.
    Keeps 80 ms before and 120 ms after detected activity.
    """
    pcm = np.frombuffer(raw, dtype="<i2")
    if not len(pcm):
        return raw
    window = max(1, sample_rate // 50)  # 20 ms
    valid_count = (len(pcm) // window) * window
    if not valid_count:
        return raw
    peaks = np.max(np.abs(pcm[:valid_count].astype(np.int32)).reshape(-1, window), axis=1)
    active = np.flatnonzero(peaks > threshold)
    if not len(active):
        return raw
    before = max(0, int(active[0]) * window - int(sample_rate * 0.08))
    after = min(len(pcm), (int(active[-1]) + 1) * window + int(sample_rate * 0.12))
    return pcm[before:after].tobytes()


def decode_wav_pcm16(data: bytes, expected_rate: int) -> bytes:
    """Decode a mono 16-bit PCM WAV; reject unexpected formats/rates."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                raise ValueError("Sayro WAV must be uncompressed mono PCM16")
            if wav.getframerate() != expected_rate:
                raise ValueError(
                    f"Unexpected Sayro WAV rate {wav.getframerate()} (expected {expected_rate})"
                )
            return wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError("Sayro did not return a valid PCM WAV") from exc