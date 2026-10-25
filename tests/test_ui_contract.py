"""Independent API contract tests; optionally verify adjacent Vue source in combined checkout."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from voice_agent.admin import app
from voice_agent.configuration import load

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
WORKER = Path(__file__).resolve().parents[1] / "voice_agent" / "worker.py"
ROUTES = [
    ('POST', '/api/login'), ('GET', '/api/session'), ('POST', '/api/logout'),
    ('GET', '/api/config'), ('GET', '/api/overview'), ('GET','/api/capabilities'),
    ('POST', '/api/rooms'), ('POST', '/api/tts/preview'),
    ('GET','/api/experience'), ('POST','/api/conversations'),('GET','/api/conversations'),
    ('POST','/api/voice-notes/transcribe'),
    ('PATCH', '/api/settings'), ('POST', '/api/providers/activate')
]

def test_api_routes_are_defined():
    methods = {(r.path, method) for r in app.routes if hasattr(r, "methods") for method in r.methods}
    for method, path in ROUTES:
        assert (path, method) in methods, f"Missing backend {method} {path}"

def test_backend_capabilities_is_authenticated():
    with TestClient(app) as client:
        assert client.get('/api/capabilities').status_code == 401

def test_dual_tts_is_active():
    cfg = load()
    assert cfg['providers']['tts']['dual_gateway']['type'] == 'dual_gateway_stream'

def test_worker_uses_room_text_and_model_dispatch():
    worker = WORKER.read_text()
    assert 'RoomOptions(text_input=True, text_output=True,' in worker
    assert 'metadata.get("tts_model")' in worker
    assert 'build_tts(config, model_override=override)' in worker

@pytest.mark.skipif(not FRONTEND.is_dir(), reason="Optional sibling Vue UI not bundled in backend-only ZIP")
def test_vue_frontend_matches_http_api_contract():
    ui = '\n'.join(p.read_text() for p in FRONTEND.rglob('*') if p.suffix in ('.vue', '.ts', '.mjs')) + '\n' + (FRONTEND.parent/'scripts'/'preserve-design.mjs').read_text()
    for path in ('/api/login','/api/session','/api/logout','/api/rooms','/api/conversations',
                 '/api/experience','/api/voice-notes/transcribe','/api/tts/preview'):
        assert path in ui, f"Missing new-UI frontend binding for {path}"

@pytest.mark.skipif(not FRONTEND.is_dir(), reason="Optional sibling Vue UI not bundled in backend-only ZIP")
def test_vue_livekit_topics_match_worker():
    src = '\n'.join(p.read_text() for p in FRONTEND.rglob('*') if p.suffix in ('.vue', '.ts'))
    assert "sendText(msg.content,{topic:'lk.chat'})" in src
    assert "registerTextStreamHandler('lk.transcription'" in src
    assert 'RoomEvent.ParticipantAttributesChanged' in src
    assert "'lk.agent.state'" in src
