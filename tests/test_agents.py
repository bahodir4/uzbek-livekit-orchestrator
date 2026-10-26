"""The four specialised agents: definitions, tools, storage, and compatibility with older persona ids."""
import asyncio
import sqlite3
from datetime import timedelta

import pytest
from livekit.agents import llm
from livekit.agents.llm import ToolContext

from voice_agent import agent_tools as T, conversations, records
from voice_agent.experience import (PERSONAS, DEFAULT_PERSONA, agent_instructions, canonical_persona,
                                    check_preferences)


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_RECORDS_DB_PATH', str(tmp_path / 'records.sqlite3'))
    monkeypatch.setenv('CONVERSATIONS_DB_PATH', str(tmp_path / 'history.sqlite3'))


def run(coro):
    return asyncio.run(coro)


def next_weekday(days_ahead=3):
    day = T._today() + timedelta(days=days_ahead)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def test_four_distinct_female_agents_with_their_own_tools():
    assert set(PERSONAS) == {'sofiya', 'malika', 'dilnoza', 'zarina'}
    primary = set()
    for pid, agent in PERSONAS.items():
        assert agent['voice_gender'] == 'female' and agent['greeting'].startswith('Assalomu alaykum')
        assert agent['name'] in agent['greeting'] and agent['name'] in agent['instructions']
        names = set(ToolContext(T.tools_for(pid)).function_tools)
        assert len(names) >= 3
        primary |= names - {'current_date_time'}
    assert len(primary) == 9                        # no tool is shared between agents (except the clock)


def test_legacy_persona_ids_still_work():
    assert canonical_persona('sofia') == 'sofiya' and canonical_persona('leyla') == 'malika'
    assert canonical_persona('nobody') == DEFAULT_PERSONA and canonical_persona(None) == DEFAULT_PERSONA
    for legacy in ('john', 'daniyar', 'leila', 'leyla', 'sofia'):
        check_preferences(legacy, 'uz', 'navoiy')
        assert 'Sofiya' in agent_instructions(legacy, 'uz', 'base') or 'Malika' in agent_instructions(legacy, 'uz', 'base')


def test_loan_deposit_savings_math():
    q = T.loan_quote(10_000_000, 12, 24)
    assert round(q['monthly']) == 945596
    assert round(T.loan_quote(10_000_000, 12, 24, 'differensial')['first']) == 1033333
    assert round(T.loan_quote(12_000_000, 12, 0)['monthly']) == 1_000_000
    assert round(T.deposit_quote(10_000_000, 12, 18)['final']) == 11956182
    assert round(T.savings_plan(12_000_000, 12)['monthly']) == 1_000_000
    assert T.savings_plan(1000, 12, 0, 5000)['reached'] is True
    for bad in ((-1, 12, 10), (1000, 0, 10), (1000, 12, 999)):
        with pytest.raises(ValueError):
            T.loan_quote(*bad)


def test_tools_return_spoken_text_and_never_raise():
    out = run(T.calculate_loan(None, 10_000_000, 12, 24))
    assert 'taxminan 945596' in out and 'taxminiy' in out
    assert 'noto' in run(T.calculate_loan(None, -5, 12, 24))           # validation => polite message, not an exception
    assert 'pasport' in run(T.loan_documents_checklist(None, 'ipoteka'))
    assert 'noto' in run(T.loan_documents_checklist(None, 'zargarlik'))
    assert 'taxminan' in run(T.calculate_deposit(None, 5_000_000, 6, 15))
    assert 'har oy' in run(T.savings_goal_plan(None, 6_000_000, 6))
    assert '20' in run(T.current_date_time(None))


def test_support_ticket_roundtrip():
    out = run(T.create_support_ticket(None, 'karta', 'Karta ishlamayapti', '+998 90 123 45 67'))
    assert '100001' in out
    assert 'qabul qilindi' in run(T.check_ticket_status(None, 100001))
    assert 'topilmadi' in run(T.check_ticket_status(None, 999999))
    assert 'noto' in run(T.create_support_ticket(None, 'karta', 'x', '12'))      # bad phone
    assert 'noto' in run(T.create_support_ticket(None, 'karta', '  ', '901234567'))


def test_appointments_capacity_cancel_and_rules():
    day = next_weekday()
    assert '09:00' in run(T.available_slots(None, day))
    first = run(T.book_appointment(None, day, '09:00', 'Ali', '901234567', 'kredit'))
    assert 'tasdiqlandi' in first and '200001' in first
    run(T.book_appointment(None, day, '09:00', 'Vali', '931112233', 'omonat'))
    assert 'band' in run(T.book_appointment(None, day, '09:00', 'Gulya', '977778899', 'kredit'))   # capacity 2
    assert '09:00' not in run(T.available_slots(None, day))
    assert 'mos kelmadi' in run(T.cancel_appointment(None, 200001, '999999999'))                    # wrong phone
    assert 'bekor qilindi' in run(T.cancel_appointment(None, 200001, '901234567'))
    assert '09:00' in run(T.available_slots(None, day))
    assert 'noto' in run(T.book_appointment(None, day, '13:00', 'Ali', '901234567', 'x'))           # lunch break
    assert 'dam olish' in run(T.available_slots(None, _next_saturday()))
    assert "o'tib" in run(T.available_slots(None, (T._today() - timedelta(days=1)).isoformat()))


def _next_saturday():
    day = T._today()
    while day.weekday() != 5:
        day += timedelta(days=1)
    return day.isoformat()


def test_storage_failure_becomes_a_polite_message(monkeypatch):
    monkeypatch.setenv('AGENT_RECORDS_DB_PATH', '/proc/nope/records.sqlite3')
    out = run(T.create_support_ticket(None, 'karta', 'Muammo', '901234567'))
    assert 'saqlab bo' in out                       # no exception reaches the call


def test_conversations_kind_migration_and_separation(tmp_path, monkeypatch):
    old = tmp_path / 'old.sqlite3'
    con = sqlite3.connect(old)
    con.execute('''CREATE TABLE conversations (id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
        persona TEXT NOT NULL, language TEXT NOT NULL, tts_model TEXT NOT NULL, created_at REAL NOT NULL,
        updated_at REAL NOT NULL, call_seconds INTEGER NOT NULL DEFAULT 0)''')
    con.execute("INSERT INTO conversations VALUES ('a','u','Старый','john','ru','sayro',1,1,0)")
    con.commit(); con.close()
    monkeypatch.setenv('CONVERSATIONS_DB_PATH', str(old))
    items = {i['id']: i for i in conversations.list_items('u')}
    assert items['a']['kind'] == 'chat'                                       # old rows survive as chats
    call = conversations.create('u', 'sofiya', 'uz', 'navoiy', 'call')
    chat = conversations.create('u', 'sofiya', 'uz', 'navoiy')
    conversations.append('u', call['id'], 'seg1', 'user', 'transcription', 'Assalomu alaykum, kredit kerak', True)
    conversations.append('u', chat['id'], 'm1', 'user', 'chat', 'Salom, omonat haqida', True)
    kinds = {i['id']: (i['kind'], i['message_count'], i['title']) for i in conversations.list_items('u')}
    assert kinds[call['id']][:2] == ('call', 1) and kinds[chat['id']][:2] == ('chat', 1)
    assert kinds[call['id']][2].startswith('Assalomu') and kinds[chat['id']][2].startswith('Salom')


def test_history_default_path_is_the_persistent_volume(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations.os, 'access', lambda *_: True)
    monkeypatch.setattr(conversations.Path, 'is_dir', lambda self: str(self) == '/app/data')
    assert conversations._default_db_path() == '/app/data/neurotech-conversations.sqlite3'


def test_model_sees_the_descriptions_from_tools_yaml():
    tools = ToolContext(T.tools_for('dilnoza')).function_tools
    schema = llm.utils.build_legacy_openai_schema(tools['available_slots'], internally_tagged=True)
    assert '09:00-17:00' in schema['description'] and 'YYYY-OO-KK' in schema['description']
    assert set(schema['parameters']['properties']) == {'date_iso'}                 # `context` is not exposed to the model
