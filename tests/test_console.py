"""CPU-only control-plane tests: no LiveKit server, GPU, or external model needed."""
import copy
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
from fastapi.testclient import TestClient

from voice_agent import admin
from voice_agent.configuration import load, save_atomic


@pytest.fixture()
def console(monkeypatch):
    with TemporaryDirectory() as dirname:
        save_atomic(copy.deepcopy(load()), Path(dirname) / "agent.yaml")
        monkeypatch.setenv("AGENT_CONFIG_PATH", str(Path(dirname) / "agent.yaml"))
        monkeypatch.setenv("ADMIN_USERNAME", "test-operator")
        monkeypatch.setenv("ADMIN_PASSWORD", "safe-admin-password-for-tests")
        monkeypatch.setenv("TTS_API_KEY", "test-key-not-production")
        monkeypatch.setenv("UI_SECURE_COOKIE", "false")
        admin._sessions.clear()
        with TestClient(admin.app) as client:
            yield client
        admin._sessions.clear()


def authenticate(client):
    r = client.post('/api/login', json={"username": "test-operator", "password": "safe-admin-password-for-tests"})
    assert r.status_code == 200, r.text
    return {"X-Voice-UI": "1"}


def test_login_and_cookie_authorization(console):
    assert console.get('/api/config').status_code == 401
    assert console.post('/api/login', json={"username": "bad", "password": "bad"}).status_code == 401
    authenticate(console)
    assert console.get('/api/session').json()["authenticated"] is True
    cfg = console.get('/api/config')
    assert cfg.status_code == 200
    assert 'test-key-not-production' not in cfg.text
    assert cfg.json()['providers']['tts']['dual_gateway']['type'] == 'dual_gateway_stream'
    assert console.post('/api/logout', headers={"X-Voice-UI": "1"}).status_code == 200
    assert console.get('/api/config').status_code == 401


def test_settings_validation_and_csrf(console):
    headers = authenticate(console)
    assert console.patch('/api/settings', json={"temperature": 0.6}).status_code == 403
    r = console.patch('/api/settings', json={"temperature": 0.6, "tts_model": "navoiy",
                                          "interruption_enabled": False}, headers=headers)
    assert r.status_code == 200, r.text
    data = load()
    assert data['providers']['llm']['local_vllm']['temperature'] == 0.6
    assert data['providers']['tts']['dual_gateway']['model'] == 'navoiy'
    assert data['turn']['interruption_enabled'] is False
    assert console.patch('/api/settings', json={"tts_model": "random"}, headers=headers).status_code == 422
    assert console.patch('/api/settings', json={"endpointing_min_delay": 9,
                                                      "endpointing_max_delay": 1}, headers=headers).status_code == 422


def test_activate_validation(console):
    headers=authenticate(console)
    assert console.post('/api/providers/activate', json={"category":"tts", "provider":"does-not-exist"},
                        headers=headers).status_code == 400
    ok = console.post('/api/providers/activate', json={"category":"tts", "provider":"sayro_primary"},
                        headers=headers)
    assert ok.status_code == 200
    assert load()['active']['tts'] == 'sayro_primary'
    r = console.post('/api/rooms',json={"model":"navoiy"},headers=headers)
    assert r.status_code == 409  # legacy provider doesn't support per-room model override


def test_preview_stream_for_both_models(console, monkeypatch):
    headers = authenticate(console)
    requests=[]
    def fake_handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"X-Audio-Sample-Rate": "24000", "X-Audio-Channels": "1",
                       "X-Audio-Format": "pcm_s16le", "Content-Type": "application/octet-stream"},
                       content=b"\x01\x00\x02\x00")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(admin.httpx, "AsyncClient",
                        lambda *a,**kw: real_client(*a,transport=httpx.MockTransport(fake_handler),**kw))
    for model in ('sayro','navoiy'):
        result=console.post('/api/tts/preview',json={"text":"Assalomu alaykum!","model":model},headers=headers)
        assert result.status_code == 200,result.text
        assert result.content == b"\x01\x00\x02\x00"
        assert result.headers['x-audio-sample-rate']=='24000'
        assert result.headers['x-tts-model']==model
    assert len(requests)==2
    assert all(x.headers['authorization']=='Bearer test-key-not-production' for x in requests)
    assert [x.read().decode() for x in requests][0].find('"sayro"')!=-1


def test_preview_rejects_wrong_audio_contract(console,monkeypatch):
    headers=authenticate(console)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(admin.httpx, "AsyncClient",
                        lambda *a,**kw: real_client(*a,transport=httpx.MockTransport(
                            lambda r:httpx.Response(200,headers={"content-type":"audio/wav"},content=b'RIFF')),
                                                     **kw))
    r=console.post('/api/tts/preview',json={"text":"Salom","model":"sayro"},headers=headers)
    assert r.status_code==502


def test_capabilities_and_livekit_text_contract(console):
    headers=authenticate(console)
    result=console.get('/api/capabilities',headers=headers)
    assert result.status_code==200
    body=result.json()
    assert body['text_chat']['topic']=='lk.chat'
    assert body['transcription']['topic']=='lk.transcription'
    assert body['agent_state']['key']=='lk.agent.state'
    assert body['tts']['models']==['sayro','navoiy']
    assert body['per_room_model_selection'] is True
    assert body['tts']['sample_rate']==24000
