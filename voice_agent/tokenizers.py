"""Sentence tokenizer for streamed LLM text that releases the first chunk of a reply as early as possible.

LiveKit's default (blingfire) tokenizer emits a sentence only after the *next* sentence has started and merges anything
shorter than 20 characters, so a short opener ("Xo'p.") is held back until a second sentence is under way. This one
cuts at the first usable boundary instead:

  * first chunk of a reply: a sentence end after >= ``first_sentence_min`` characters, a clause end (, ; :) after
    >= ``first_clause_min`` characters, or, with no punctuation at all, a word boundary at ``first_max`` characters (a last resort: it cuts a thought in two)
  * later chunks: a sentence end once ``min_chars`` characters have accumulated (short sentences are merged so the
    TTS is not called for tiny pieces), a clause end after ``later_clause_min``, or a word boundary at ``max_chars``

Every pushed character is returned unchanged, whitespace included: the concatenation of all chunks equals the input,
so the transcript shown in the UI keeps its spacing. A boundary needs whitespace after the punctuation, which keeps
numbers such as "3.5" or "12,5" in one piece.
"""
from __future__ import annotations

import asyncio
import re

from livekit.agents import utils
from livekit.agents.tokenize import tokenizer

_SENTENCE = re.compile(r"[.!?…]+[\"')»”\]]*\s+")
_CLAUSE = re.compile(r"[,;:]\s+")
IDLE_FLUSH_SECONDS = 0.15      # a finished sentence is released this long after the last text, without waiting for more
MIN_IDLE_CHARS = 8


class FastSentenceTokenizer(tokenizer.SentenceTokenizer):
    def __init__(self, *, first_sentence_min: int = 12, first_clause_min: int = 20, first_max: int = 120,
                 min_chars: int = 60, later_clause_min: int = 140, max_chars: int = 300) -> None:
        self._cfg = (first_sentence_min, first_clause_min, first_max, min_chars, later_clause_min, max_chars)

    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        chunks, buf, first = [], text, True
        while (cut := find_cut(buf, first, self._cfg)) is not None:
            chunks.append(buf[:cut])
            buf, first = buf[cut:], False
        if buf:
            chunks.append(buf)
        return chunks

    def stream(self, *, language: str | None = None) -> tokenizer.SentenceStream:
        return _Stream(self._cfg)


def find_cut(buf: str, first: bool, cfg: tuple[int, int, int, int, int, int]) -> int | None:
    """Index just after the next chunk in ``buf`` (the chunk keeps its trailing whitespace), or None to keep waiting."""
    first_sentence_min, first_clause_min, first_max, min_chars, later_clause_min, max_chars = cfg
    sentence_min = first_sentence_min if first else min_chars
    clause_min = first_clause_min if first else later_clause_min
    limit = first_max if first else max_chars
    candidates = [m.end() for m in _SENTENCE.finditer(buf) if m.end() >= sentence_min][:1]
    candidates += [m.end() for m in _CLAUSE.finditer(buf) if m.end() >= clause_min][:1]
    if candidates:
        return min(candidates)
    if len(buf) >= limit:
        space = buf.rfind(" ", 0, limit)
        if space > 0:
            return space + 1
    return None


class _Stream(tokenizer.SentenceStream):
    def __init__(self, cfg) -> None:
        super().__init__()
        self._cfg = cfg
        self._buf = ""
        self._first = True
        self._segment = utils.shortuuid()
        self._idle: asyncio.TimerHandle | None = None

    def _cancel_idle(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None

    def _arm_idle(self) -> None:
        """A sentence that ends the text so far (the LLM goes on to a tool call, or pauses) must not wait for the
        end of the whole LLM response: release it after IDLE_FLUSH_SECONDS without new text."""
        text = self._buf.rstrip()
        if len(text) < MIN_IDLE_CHARS or text[-1] not in ".!?…" or (len(text) > 1 and text[-2].isdigit()):
            return
        try:
            self._idle = asyncio.get_running_loop().call_later(IDLE_FLUSH_SECONDS, self._idle_flush)
        except RuntimeError:                      # no running loop (plain synchronous use): nothing to schedule
            self._idle = None

    def _idle_flush(self) -> None:
        self._idle = None
        if self._event_ch.closed or not self._buf.strip():
            return
        chunk, self._buf = self._buf, ""
        self._first = False
        self._emit(chunk)

    def _emit(self, chunk: str) -> None:
        self._event_ch.send_nowait(tokenizer.TokenData(token=chunk, segment_id=self._segment))

    def _drain(self) -> None:
        while True:
            cut = find_cut(self._buf, self._first, self._cfg)
            if cut is None:
                return
            chunk, self._buf = self._buf[:cut], self._buf[cut:]
            self._first = False
            self._emit(chunk)

    def push_text(self, text: str) -> None:
        self._check_not_closed()
        self._cancel_idle()
        self._buf += text
        self._drain()
        if self._buf:
            self._arm_idle()

    def flush(self) -> None:
        self._check_not_closed()
        self._cancel_idle()
        if self._buf:
            chunk, self._buf = self._buf, ""
            self._emit(chunk)
        self._first = True
        self._segment = utils.shortuuid()

    def end_input(self) -> None:
        self.flush()
        self._do_close()

    async def aclose(self) -> None:
        self._cancel_idle()
        self._do_close()
