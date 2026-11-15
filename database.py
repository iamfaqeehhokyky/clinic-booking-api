import json
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


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


def normalize_database_url(url):
    # Render may supply postgres:// or postgresql://. Use psycopg 3 explicitly.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def init_db(app):
    db.init_app(app)
    with app.app_context():
        # Creates missing tables. Use Flask-Migrate for future schema changes.
        db.create_all()


def get_db(_app=None):
    # Flask-SQLAlchemy provides a request/app-context scoped session.
    return db.session


def close_db(_exception=None):
    # Flask-SQLAlchemy removes its scoped session at app-context teardown.
    db.session.remove()
