"""Additional contract tests for exact dist.zip controls and server settings."""
from fastapi.testclient import TestClient
from voice_agent.admin import app


def test_ui_persona_and_preferences(monkeypatch,tmp_path):
    monkeypatch.setenv('CONVERSATIONS_DB_PATH',str(tmp_path/'prefs.sqlite3'))
    monkeypatch.setenv('ADMIN_USERNAME','tester')
    monkeypatch.setenv('ADMIN_PASSWORD','long-unit-testing-password')
    monkeypatch.setenv('UI_SECURE_COOKIE','false')
    with TestClient(app) as client:
        assert client.post('/api/login',json={'username':'tester','password':'long-unit-testing-password'}).status_code==200
        session=client.get('/api/session').json()
        assert session['authenticated'] is True and session['username']=='tester'
        assert {'john','daniyar','leila','sofia'} <= {p['id'] for p in client.get('/api/experience').json()['personas']}
        body={'persona_id':'leila','language':'auto','tts_model':'navoiy','voice_enabled':True,'rate':1.25,'pitch':0.9}
        assert client.put('/api/preferences',json=body,headers={'X-Voice-UI':'1'}).status_code==200
        assert client.get('/api/preferences').json()==body
        assert client.put('/api/preferences',json=body).status_code==403
        conv=client.post('/api/conversations',json={'persona':'john','language':'en','tts_model':'sayro'},headers={'X-Voice-UI':'1'})
        assert conv.status_code==201
        r=client.post('/api/rooms',json={'conversation_id':conv.json()['id'],'persona':'john','language':'en','model':'sayro'},headers={'X-Voice-UI':'1'})
        assert r.status_code==422 and 'Uzbek only' in str(r.json())
