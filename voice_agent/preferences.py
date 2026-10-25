"""Persist original UI voice/language/persona preferences, user-scoped."""
from __future__ import annotations
import json
from .conversations import database


def get(owner: str):
    with database() as db:
        db.execute('CREATE TABLE IF NOT EXISTS user_preferences (owner TEXT PRIMARY KEY, json TEXT NOT NULL)')
        result = db.execute('SELECT json FROM user_preferences WHERE owner=?', (owner,)).fetchone()
        return json.loads(result['json']) if result else None


def put(owner: str, data: dict):
    with database() as db:
        db.execute('CREATE TABLE IF NOT EXISTS user_preferences (owner TEXT PRIMARY KEY, json TEXT NOT NULL)')
        db.execute('INSERT INTO user_preferences (owner,json) VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET json=excluded.json',
                   (owner,json.dumps(data,ensure_ascii=False)))
    return data
