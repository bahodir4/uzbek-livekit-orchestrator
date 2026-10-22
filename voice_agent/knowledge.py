"""Constrained, local, read-only reference search: no arbitrary shell/network tools."""
from __future__ import annotations
import asyncio
import re
from pathlib import Path

WORD_RE = re.compile(r"[\wʻʼ’-]+", flags=re.UNICODE)
ALLOWED_EXTS = {".md", ".txt"}


def terms(text: str) -> set[str]:
    return {word.lower() for word in WORD_RE.findall(text) if len(word) > 2}


class LocalKnowledgeStore:
    def __init__(self, directory: Path, *, max_results: int = 3,
                 max_chars_per_result: int = 1100, max_files: int = 100):
        self.directory = directory.resolve()
        self.max_results = max_results
        self.max_chars_per_result = max_chars_per_result
        self.max_files = max_files

    def _search(self, question: str) -> str:
        if not self.directory.is_dir():
            return "Hozircha ma'lumotlar bazasi mavjud emas."
        query = terms(question)
        if not query:
            return "Qidiruv uchun aniqroq savol bering."
        matches = []
        paths = sorted(p for p in self.directory.rglob('*') if p.is_file() and p.suffix.lower() in ALLOWED_EXTS)
        for file in paths[:self.max_files]:
            resolved = file.resolve()
            if not resolved.is_relative_to(self.directory) or file.stat().st_size > 1024 * 1024:
                continue
            try:
                content = file.read_text('utf-8')
            except (OSError, UnicodeError):
                continue
            # Paragraph ranking, no unrestricted directory/file access.
            for paragraph in re.split(r"\n\s*\n", content):
                overlap = query & terms(paragraph)
                if overlap:
                    score = len(overlap) + (1 if query <= terms(paragraph) else 0)
                    matches.append((score, file.name, paragraph[:self.max_chars_per_result]))
        matches.sort(key=lambda x: x[0], reverse=True)
        if not matches:
            return "Taqdim etilgan hujjatlarda bu savolga javob topilmadi. Javobni taxmin qilmang."
        return "\n\n".join(f"Manba: {name}\n{snippet}" for _, name, snippet in matches[:self.max_results])

    async def search(self, question: str) -> str:
        return await asyncio.to_thread(self._search, question)
