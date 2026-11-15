import json
import sqlite3
from flask import g
from datetime import datetime, timezone

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def parse_datetime(value):
    if not isinstance(value, str):
        raise ValueError("not a string")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)

def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def connect_db(path):
    db = sqlite3.connect(path, timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 15000")
    return db

def get_db(app):
    # One connection per Flask application context/request; never share it across threads.
    if "db" not in g:
        g.db = connect_db(app.config["DATABASE"])
    return g.db

def close_db(_exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db(path):
    db = connect_db(path)
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS slots (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        start TEXT NOT NULL,
        end TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS bookings (
        id TEXT PRIMARY KEY,
        slot_id TEXT NOT NULL REFERENCES slots(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('confirmed', 'cancelled')),
        created_at TEXT NOT NULL
    );

    CREATE UNIQUE INDEX IF NOT EXISTS one_confirmed_booking_per_slot
    ON bookings(slot_id) WHERE status='confirmed';

    CREATE INDEX IF NOT EXISTS bookings_email_order
    ON bookings(email, created_at, id);

    CREATE TABLE IF NOT EXISTS idempotency (
        idem_key TEXT PRIMARY KEY,
        body_hash TEXT NOT NULL,
        status_code INTEGER NOT NULL,
        response_body TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS rate_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        attempted_at REAL NOT NULL
    );

    CREATE INDEX IF NOT EXISTS rate_attempts_email_time
    ON rate_attempts(email, attempted_at);
    """)
    db.close()
