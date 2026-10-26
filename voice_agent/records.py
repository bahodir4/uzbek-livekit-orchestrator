"""Small SQLite store for what the agents create during a call: support tickets and appointments.

Shared by the worker (writes) and, if wanted later, the API (reads) through the same data volume.
Every public function returns plain data or raises RecordsError; the agent tools turn that into a
spoken apology, so a storage problem can never break a live call.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class RecordsError(Exception):
    """Storage unavailable or request rejected; message is safe to log."""


def _db_path() -> Path:
    configured = os.getenv("AGENT_RECORDS_DB_PATH")
    if configured:
        return Path(configured)
    data_dir = Path("/app/data")
    return (data_dir if data_dir.is_dir() and os.access(data_dir, os.W_OK) else Path("/tmp")) / "agent_records.sqlite3"


@contextmanager
def _connect():
    path = _db_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path), timeout=5)
    except (OSError, sqlite3.Error) as exc:
        raise RecordsError(f"cannot open records database: {exc}") from exc
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("""CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, category TEXT NOT NULL,
            summary TEXT NOT NULL, phone TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'qabul qilindi')""")
        con.execute("""CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, day TEXT NOT NULL, slot TEXT NOT NULL,
            full_name TEXT NOT NULL, phone TEXT NOT NULL, purpose TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'tasdiqlangan')""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_appt_day ON appointments(day, slot, status)")
        from . import agent_registry
        starts = agent_registry.get().settings["records"]
        for table, start in (("tickets", starts["ticket_start"]), ("appointments", starts["appointment_start"])):
            # AUTOINCREMENT keeps numbers readable over the phone: start from a fixed 6-digit base.
            if con.execute("SELECT 1 FROM sqlite_sequence WHERE name=?", (table,)).fetchone() is None:
                con.execute("INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)", (table, start - 1))
        con.commit()  # setup done; callers may open their own explicit transaction
        yield con
        con.commit()
    except sqlite3.Error as exc:
        raise RecordsError(f"records database error: {exc}") from exc
    finally:
        con.close()


def create_ticket(category: str, summary: str, phone: str) -> int:
    with _connect() as db:
        cur = db.execute("INSERT INTO tickets (created_at, category, summary, phone) VALUES (?,?,?,?)",
                         (time.time(), category[:60], summary[:1000], phone[:30]))
        return int(cur.lastrowid)


def get_ticket(ticket_id: int) -> dict | None:
    with _connect() as db:
        row = db.execute("SELECT id, category, summary, status, created_at FROM tickets WHERE id=?",
                         (ticket_id,)).fetchone()
        return dict(row) if row else None


def booked_slots(day: str) -> dict[str, int]:
    with _connect() as db:
        rows = db.execute("SELECT slot, COUNT(*) n FROM appointments WHERE day=? AND status='tasdiqlangan' "
                          "GROUP BY slot", (day,)).fetchall()
        return {row["slot"]: int(row["n"]) for row in rows}


def create_appointment(day: str, slot: str, full_name: str, phone: str, purpose: str, capacity: int) -> int | None:
    """Insert unless the slot is already full (checked in one transaction). None = slot full."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        taken = db.execute("SELECT COUNT(*) FROM appointments WHERE day=? AND slot=? AND status='tasdiqlangan'",
                           (day, slot)).fetchone()[0]
        if taken >= capacity:
            return None
        cur = db.execute("INSERT INTO appointments (created_at, day, slot, full_name, phone, purpose) "
                         "VALUES (?,?,?,?,?,?)",
                         (time.time(), day, slot, full_name[:80], phone[:30], purpose[:300]))
        return int(cur.lastrowid)


def cancel_appointment(appointment_id: int, phone: str) -> bool:
    """Cancel only when the phone number matches the booking (no cancelling other people's visits)."""
    with _connect() as db:
        cur = db.execute("UPDATE appointments SET status='bekor qilingan' WHERE id=? AND phone=? "
                         "AND status='tasdiqlangan'", (appointment_id, phone[:30]))
        return cur.rowcount > 0
