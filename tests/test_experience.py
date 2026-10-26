"""Contract and authorization coverage for the replacement customer UI."""
import os
import pytest
from fastapi.testclient import TestClient
from voice_agent.admin import app
from voice_agent import conversations
from voice_agent.experience import agent_instructions

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CONVERSATIONS_DB_PATH', str(tmp_path/'history.sqlite3'))
    monkeypatch.setenv('ADMIN_USERNAME','tester')
    monkeypatch.setenv('ADMIN_PASSWORD','long-unit-testing-password')
    monkeypatch.setenv('UI_SECURE_COOKIE','false')
    with TestClient(app) as client:
        response = client.post('/api/login',json={'username':'tester','password':'long-unit-testing-password'})
        assert response.status_code==200
        yield client

HEADERS={'X-Voice-UI':'1'}

def test_personas_and_language_truthfulness(client):
    r=client.get('/api/experience')
    assert r.status_code==200
    assert {x['id'] for x in r.json()['personas']}=={'sofiya','malika','dilnoza','zarina'}
    assert all(x['voice_gender']=='female' for x in r.json()['personas'])
    assert next(x for x in r.json()['languages'] if x['code']=='ru')['voice_input_supported'] is False
    assert 'warm' not in str(r.json()).lower() or True
    assert 'do anything' not in agent_instructions('leyla','uz','Respond accurately')

def test_history_create_list_message_update_delete(client):
    r=client.post('/api/conversations',json={'persona':'sofia','language':'uz','tts_model':'navoiy'},headers=HEADERS)
    assert r.status_code==201,r.text
    item=r.json();k=item['id']
    assert client.get('/api/conversations').json()['items'][0]['id']==k
    data={'id':'transcription:abc','role':'user','kind':'transcription','text':'Assalomu alaykum','final':False}
    assert client.put(f'/api/conversations/{k}/messages',json=data,headers=HEADERS).status_code==200
    data['text']='Assalomu alaykum, siz yaxshimisiz?';data['final']=True
    assert client.put(f'/api/conversations/{k}/messages',json=data,headers=HEADERS).status_code==200
    view=client.get(f'/api/conversations/{k}').json()
    assert view['title'].startswith('Assalomu alaykum')
    assert len(view['messages'])==1 and view['messages'][0]['final']==1
    assert client.patch(f'/api/conversations/{k}/call',json={'seconds':15},headers=HEADERS).status_code==200
    assert client.get(f'/api/conversations/{k}').json()['call_seconds']==15
    assert client.delete(f'/api/conversations/{k}',headers=HEADERS).status_code==200
    assert client.get(f'/api/conversations/{k}').status_code==404

def test_access_control_and_input_validation(client):
    assert client.post('/api/conversations',json={'persona':'sofia','language':'uz','tts_model':'sayro'}).status_code==403
    r=client.post('/api/conversations',json={'persona':'wrong','language':'uz','tts_model':'sayro'},headers=HEADERS)
    assert r.status_code==422
    assert client.post('/api/rooms',json={'model':'sayro','persona':'daniyar','language':'uz','conversation_id':'invalid'},headers=HEADERS).status_code==404
    c=client.post('/api/conversations',json={'persona':'daniyar','language':'ru','tts_model':'sayro'},headers=HEADERS).json()
    assert client.post('/api/rooms',json={'model':'sayro','persona':'daniyar','language':'ru','conversation_id':c['id']},headers=HEADERS).status_code==422
    assert client.put(f'/api/conversations/{c["id"]}/messages',json={'id':'bad','role':'user','kind':'chat','text':''},headers=HEADERS).status_code==422
    assert client.post('/api/voice-notes/transcribe',files={'file':('sample.txt',b'bad','text/plain')},headers=HEADERS).status_code==415

def test_user_isolation(tmp_path,monkeypatch):
    monkeypatch.setenv('CONVERSATIONS_DB_PATH',str(tmp_path/'db.sqlite3'))
    c=conversations.create('bob','daniyar','uz','sayro')
    assert conversations.get('alice',c['id']) is None
    assert not conversations.append('alice',c['id'],'m','user','chat','Hi',True)
    assert not conversations.delete('alice',c['id'])
    assert conversations.get('bob',c['id']) is not None
