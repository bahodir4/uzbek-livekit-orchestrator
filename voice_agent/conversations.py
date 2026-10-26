"""Simple persistent history for the customer-facing conversation UI.

Authorizes every record by authenticated username; no SQL identifiers are derived
from clients. Keep data volume bounded and use a persistent Docker volume.
"""
from __future__ import annotations
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

def _default_db_path() -> str:
    # History must survive container re-creation: use the persistent data volume when it is mounted.
    data = Path('/app/data')
    if data.is_dir() and os.access(data, os.W_OK):
        return str(data / 'neurotech-conversations.sqlite3')
    return '/tmp/neurotech-conversations.sqlite3'


DB_PATH = Path(os.getenv('CONVERSATIONS_DB_PATH', _default_db_path()))
NEW_TITLE = 'Yangi suhbat'
LEGACY_TITLES = ('Новый разговор', NEW_TITLE)

@contextmanager
def database():
    path = Path(os.getenv('CONVERSATIONS_DB_PATH', str(DB_PATH)))
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    con.execute('PRAGMA busy_timeout=5000')
    con.execute('''CREATE TABLE IF NOT EXISTS conversations (
      id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
      persona TEXT NOT NULL, language TEXT NOT NULL, tts_model TEXT NOT NULL,
      created_at REAL NOT NULL, updated_at REAL NOT NULL,
      call_seconds INTEGER NOT NULL DEFAULT 0
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS messages (
      id TEXT NOT NULL, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
      role TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
      final INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
      PRIMARY KEY (conversation_id, id)
    )''')
    if 'kind' not in {row[1] for row in con.execute('PRAGMA table_info(conversations)')}:
        # chat and call transcripts are separate conversations; older rows were all chats
        con.execute("ALTER TABLE conversations ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat'")
    con.execute('CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations(owner,updated_at DESC)')
    try:
      yield con
      con.commit()
    finally:
      con.close()


def list_items(owner: str):
    with database() as c:
        return [dict(r) for r in c.execute('''SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count
              FROM conversations c WHERE owner=? ORDER BY updated_at DESC LIMIT 150''', (owner,))]


def create(owner: str, persona: str, language: str, model: str, kind: str = 'chat'):
    now = time.time()
    item = {'id': uuid.uuid4().hex, 'owner': owner, 'title': NEW_TITLE,
            'persona': persona, 'language': language, 'tts_model': model, 'kind': kind if kind in ('chat', 'call') else 'chat',
            'created_at': now, 'updated_at': now, 'call_seconds': 0}
    with database() as c:
        c.execute('''INSERT INTO conversations
                 (id,owner,title,persona,language,tts_model,kind,created_at,updated_at,call_seconds)
                 VALUES (:id,:owner,:title,:persona,:language,:tts_model,:kind,:created_at,:updated_at,:call_seconds)''', item)
    return item


def get(owner: str, item_id: str):
    with database() as c:
        record = c.execute('SELECT * FROM conversations WHERE id=? AND owner=?', (item_id,owner)).fetchone()
        if record is None: return None
        result = dict(record)
        result['messages'] = [dict(r) for r in c.execute('''SELECT id,role,kind,text,final,created_at
                   FROM messages WHERE conversation_id=? ORDER BY created_at, rowid LIMIT 1500''',(item_id,))]
        return result


def append(owner: str, item_id: str, msg_id: str, role: str, kind: str, text: str, final: bool):
    now=time.time()
    with database() as c:
        if not c.execute('SELECT 1 FROM conversations WHERE id=? AND owner=?',(item_id,owner)).fetchone():
            return False
        c.execute('''INSERT INTO messages (id,conversation_id,role,kind,text,final,created_at)
                     VALUES (?,?,?,?,?,?,?) ON CONFLICT(conversation_id,id) DO UPDATE SET
                     text=excluded.text, final=excluded.final''',
                  (msg_id,item_id,role,kind,text,int(final),now))
        if role=='user' and len(text)>8:
            title=c.execute('SELECT title FROM conversations WHERE id=?',(item_id,)).fetchone()[0]
            if title in LEGACY_TITLES:
                c.execute('UPDATE conversations SET title=? WHERE id=?', (text[:75], item_id))
        c.execute('UPDATE conversations SET updated_at=? WHERE id=?',(now,item_id))
        return True


def set_call_length(owner: str, item_id: str, seconds: int):
    with database() as c:
        cur=c.execute('UPDATE conversations SET call_seconds=MAX(call_seconds, ?),updated_at=? WHERE owner=? AND id=?',
                      (seconds,time.time(),owner,item_id))
        return cur.rowcount>0


def delete(owner: str, item_id: str):
    with database() as c:
        return c.execute('DELETE FROM conversations WHERE id=? AND owner=?', (item_id,owner)).rowcount>0


def clear(owner: str):
    with database() as c:
        return c.execute('DELETE FROM conversations WHERE owner=?', (owner,)).rowcount
