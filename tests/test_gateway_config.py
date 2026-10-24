"""Prove the gateway remains a configurable remote dependency."""
from voice_agent.configuration import load


def test_gateway_url_environment_override(monkeypatch):
    monkeypatch.setenv("TTS_GATEWAY_URL", "https://remote-gateway.example:8000/")
    assert load()["providers"]["tts"]["dual_gateway"]["url"] == "https://remote-gateway.example:8000"


def test_gateway_invalid_scheme_rejected(monkeypatch):
    monkeypatch.setenv("TTS_GATEWAY_URL", "ftp://not-an-http-service")
    try:
        load()
        assert False, "Invalid scheme must be rejected"
    except ValueError as error:
        assert "TTS_GATEWAY_URL" in str(error)
