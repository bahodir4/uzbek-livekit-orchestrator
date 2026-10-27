import pytest


@pytest.fixture(autouse=True)
def isolated_tts_cache(tmp_path, monkeypatch):
    """The TTS phrase cache must never leak between tests (or touch a real cache folder)."""
    monkeypatch.setenv("GREETING_CACHE_DIR", str(tmp_path / "tts-cache"))
