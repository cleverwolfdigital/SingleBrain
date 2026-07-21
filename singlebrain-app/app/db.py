"""Thin SQLite layer. stdlib only, no ORM (keeps Phase 1 dependency-light)."""
import sqlite3
from pathlib import Path
from . import config


def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with get_conn() as conn:
        conn.executescript(schema)
    _migrate()


# Idempotent, additive migrations for columns introduced after the initial
# schema shipped (CREATE TABLE IF NOT EXISTS won't alter an existing table).
_COLUMN_MIGRATIONS = {
    "tasks": {
        "estimate_min": "INTEGER",
        "actual_sec": "INTEGER DEFAULT 0",
        "started_at": "INTEGER",
        "completed_at": "INTEGER",
        "assignee": "TEXT",       # email of the staff member the task is assigned to
        "assigned_by": "TEXT",    # email of whoever created/assigned it — who to notify on completion
        "client": "TEXT",
        "recurring_id": "INTEGER",
        "team_id": "INTEGER",     # optional team this task is assigned to (members expanded into task_assignees)
    },
    "businesses": {
        "initials": "TEXT",
        "tier": "INTEGER",
        "owner": "TEXT",
        "state": "TEXT",
        "kind": "TEXT DEFAULT 'business'",
        "parent_id": "INTEGER",
    },
    "projects": {
        "state": "TEXT",
        "badge": "TEXT",
        "kind": "TEXT DEFAULT 'project'",
        "priority": "TEXT",
    },
    "staff": {
        "email": "TEXT",
        "notes": "TEXT",
    },
    "clients": {
        "assignee": "TEXT",   # staff email who owns the client relationship
    },
    "app_users": {
        # Lets a non-admin hand work to other people (and be told when it's done)
        # without granting the whole Super Admin surface.
        "can_assign": "INTEGER DEFAULT 0",
    },
    "feedback": {
        "context": "TEXT",          # JSON: page, browser/OS, screen + viewport, timestamp
        "screenshot": "BLOB",       # optional pasted/attached image bytes
        "screenshot_mime": "TEXT",
    },
}


def _migrate():
    with get_conn() as conn:
        for table, cols in _COLUMN_MIGRATIONS.items():
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue  # table not created yet; schema.sql will create it with columns
            for col, decl in cols.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        conn.commit()


def query(sql, params=()):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def execute(sql, params=()):
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
