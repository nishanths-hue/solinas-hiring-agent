"""Thin DB access layer. SQLite for local dev; the schema is plain enough
to port to Postgres unchanged (swap sqlite3 -> psycopg2, drop AUTOINCREMENT)."""

import sqlite3
import json
import os
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "hiring.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def init_db(reset: bool = False):
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert(table: str, fields: dict) -> int:
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    vals = [json.dumps(v) if isinstance(v, (list, dict)) else v for v in fields.values()]
    with get_conn() as conn:
        cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", vals)
        return cur.lastrowid


def fetch_one(query: str, params: tuple = ()):
    with get_conn() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None


def fetch_all(query: str, params: tuple = ()):
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def log_activity(candidate_id: int, activity: str, stage_from=None, stage_to=None,
                  actor="system", is_skip=False, skip_reason=None):
    insert("activity_timeline", {
        "candidate_id": candidate_id,
        "activity": activity,
        "stage_from": stage_from,
        "stage_to": stage_to,
        "actor": actor,
        "is_stage_skip": int(is_skip),
        "skip_reason": skip_reason,
    })
