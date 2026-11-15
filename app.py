import os
from flask import Flask, jsonify, request, render_template
from database import init_db, get_db, close_db, utc_now, parse_datetime, canonical_json
from datetime import datetime, timezone, timedelta
from email_validator import validate_email, EmailNotValidError
import hashlib
import json
import uuid
from dotenv import load_dotenv

load_dotenv()

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("DATABASE_PATH", os.path.join(app.instance_path, "clinic.sqlite3")),
        API_KEY=os.environ.get("API_KEY", "dev-secret-change-me"),
        RATE_LIMIT=5,
        RATE_WINDOW_SECONDS=60,
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(os.path.dirname(app.config["DATABASE"]) or ".", exist_ok=True)
    init_db(app.config["DATABASE"])
    app.teardown_appcontext(close_db)

    def error(message, status):
        return jsonify({"error": message}), status

    @app.before_request
    def require_api_key():
        # The HTML page is public; all /api routes and documented endpoints are protected.
        if request.path == "/" or request.path.startswith("/static/"):
            return None
        if request.headers.get("X-API-Key") != app.config["API_KEY"]:
            return error("unauthorized", 401)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/reset")
    def reset_slots():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("slots"), list):
            return error("slots must be an array", 400)

        normalized = []
        seen = set()
        for slot in payload["slots"]:
            if not isinstance(slot, dict):
                return error("each slot must be an object", 400)
            for field in ("id", "provider", "start", "end"):
                if not isinstance(slot.get(field), str) or not slot[field].strip():
                    return error(f"{field} is required", 400)
            sid = slot["id"].strip()
            if sid in seen:
                return error("slot ids must be unique", 400)
            seen.add(sid)
            try:
                start = parse_datetime(slot["start"])
                end = parse_datetime(slot["end"])
            except ValueError:
                return error("start and end must be valid ISO date-times", 400)
            if end <= start:
                return error("end must be after start", 400)
            normalized.append((sid, slot["provider"].strip(), start.isoformat(), end.isoformat()))

        db = get_db(app)
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM bookings")
            db.execute("DELETE FROM slots")
            db.execute("DELETE FROM idempotency")
            db.executemany(
                "INSERT INTO slots (id, provider, start, end) VALUES (?, ?, ?, ?)",
                normalized,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        return jsonify({"message": "slots replaced", "count": len(normalized)}), 200

    @app.get("/slots")
    def list_slots():
        provider = request.args.get("provider")
        db = get_db(app)
        if provider is None:
            rows = db.execute("""
                SELECT s.*, CASE WHEN EXISTS (
                    SELECT 1 FROM bookings b
                    WHERE b.slot_id=s.id AND b.status='confirmed'
                ) THEN 0 ELSE 1 END AS available
                FROM slots s ORDER BY s.start ASC, s.id ASC
            """).fetchall()
        else:
            rows = db.execute("""
                SELECT s.*, CASE WHEN EXISTS (
                    SELECT 1 FROM bookings b
                    WHERE b.slot_id=s.id AND b.status='confirmed'
                ) THEN 0 ELSE 1 END AS available
                FROM slots s WHERE s.provider=? ORDER BY s.start ASC, s.id ASC
            """, (provider,)).fetchall()
        return jsonify({"slots": [
            {"id": r["id"], "provider": r["provider"], "start": r["start"],
             "end": r["end"], "available": bool(r["available"])}
            for r in rows
        ]})

    def email_for_rate_limit(payload):
        if isinstance(payload, dict) and isinstance(payload.get("email"), str):
            return payload["email"].strip().lower()[:320]
        return None

    def record_rate_attempt(db, email):
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=app.config["RATE_WINDOW_SECONDS"])).timestamp()
        now_ts = now.timestamp()
        db.execute("DELETE FROM rate_attempts WHERE attempted_at < ?", (cutoff,))
        rows = db.execute(
            "SELECT attempted_at FROM rate_attempts WHERE email=? AND attempted_at>=? ORDER BY attempted_at",
            (email, cutoff),
        ).fetchall()
        if len(rows) >= app.config["RATE_LIMIT"]:
            retry_after = max(1, int(rows[0]["attempted_at"] + app.config["RATE_WINDOW_SECONDS"] - now_ts + 0.999))
            return retry_after
        db.execute("INSERT INTO rate_attempts (email, attempted_at) VALUES (?, ?)", (email, now_ts))
        return None

    @app.post("/bookings")
    def create_booking():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error("request body must be a JSON object", 400)

        # Idempotency replay is checked before rate limiting so a retry returns
        # the exact original response and does not create or consume another attempt.
        idem_key = request.headers.get("Idempotency-Key")
        body_canonical = canonical_json(payload)
        body_hash = hashlib.sha256(body_canonical.encode("utf-8")).hexdigest()

        db = get_db(app)
        if idem_key:
            existing = db.execute(
                "SELECT body_hash, status_code, response_body FROM idempotency WHERE idem_key=?",
                (idem_key,),
            ).fetchone()
            if existing:
                if existing["body_hash"] != body_hash:
                    return error("Idempotency-Key was already used with a different request body", 422)
                return app.response_class(existing["response_body"], status=existing["status_code"],
                                          mimetype="application/json")

        rate_email = email_for_rate_limit(payload)
        if rate_email:
            try:
                db.execute("BEGIN IMMEDIATE")
                retry_after = record_rate_attempt(db, rate_email)
                db.commit()
            except Exception:
                db.rollback()
                raise
            if retry_after is not None:
                response, status = error("too many booking attempts; try again later", 429)
                response.headers["Retry-After"] = str(retry_after)
                return response, status

        # Level 2 validation
        for field in ("slot_id", "name", "email"):
            if not isinstance(payload.get(field), str) or not payload[field].strip():
                return error(f"{field} is required", 400)

        name = payload["name"].strip()
        email = payload["email"].strip().lower()
        slot_id = payload["slot_id"].strip()
        try:
            validate_email(email, check_deliverability=False)
        except EmailNotValidError:
            return error("email is invalid", 400)

        # BEGIN IMMEDIATE serializes writers. The unique partial index is a
        # second line of defence against two confirmed bookings for one slot.
        try:
            db.execute("BEGIN IMMEDIATE")
            slot = db.execute("SELECT * FROM slots WHERE id=?", (slot_id,)).fetchone()
            if not slot:
                result = ({"error": "slot not found"}, 404)
            else:
                start = parse_datetime(slot["start"])
                if start <= datetime.now(timezone.utc):
                    result = ({"error": "slot has already started"}, 400)
                else:
                    occupied = db.execute(
                        "SELECT id FROM bookings WHERE slot_id=? AND status='confirmed'", (slot_id,)
                    ).fetchone()
                    if occupied:
                        result = ({"error": "slot already booked"}, 409)
                    else:
                        booking = {
                            "id": str(uuid.uuid4()),
                            "slot_id": slot_id,
                            "name": name,
                            "email": email,
                            "status": "confirmed",
                            "created_at": utc_now(),
                        }
                        db.execute("""
                            INSERT INTO bookings (id, slot_id, name, email, status, created_at)
                            VALUES (?, ?, ?, ?, 'confirmed', ?)
                        """, (booking["id"], slot_id, name, email, booking["created_at"]))
                        result = (booking, 201)
            response_body = json.dumps(result[0], separators=(",", ":"))
            if idem_key:
                db.execute("""
                    INSERT INTO idempotency (idem_key, body_hash, status_code, response_body, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (idem_key, body_hash, result[1], response_body, utc_now()))
            db.commit()
        except Exception:
            db.rollback()
            raise

        return app.response_class(response_body, status=result[1], mimetype="application/json")

    @app.get("/bookings")
    def list_person_bookings():
        email = request.args.get("email")
        if not email or not email.strip():
            return error("email is required", 400)
        try:
            validate_email(email.strip(), check_deliverability=False)
        except EmailNotValidError:
            return error("email is invalid", 400)

        limit_raw = request.args.get("limit", "10")
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            return error("limit must be between 1 and 50", 400)
        if limit < 1 or limit > 50:
            return error("limit must be between 1 and 50", 400)

        cursor = request.args.get("cursor")
        db = get_db(app)
        params = [email.strip().lower()]
        where = "WHERE lower(email)=?"
        if cursor:
            # Cursor is the last booking id returned. It is scoped to the email.
            cursor_row = db.execute(
                "SELECT created_at, id FROM bookings WHERE id=? AND lower(email)=?",
                (cursor, email.strip().lower()),
            ).fetchone()
            if not cursor_row:
                return error("invalid cursor", 400)
            where += " AND (created_at > ? OR (created_at = ? AND id > ?))"
            params.extend([cursor_row["created_at"], cursor_row["created_at"], cursor_row["id"]])

        rows = db.execute(
            f"SELECT * FROM bookings {where} ORDER BY created_at ASC, id ASC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        bookings = [booking_dict(r) for r in page]
        next_cursor = page[-1]["id"] if has_more and page else None
        return jsonify({"bookings": bookings, "next_cursor": next_cursor})

    @app.get("/bookings/<booking_id>")
    def get_booking(booking_id):
        row = get_db(app).execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if not row:
            return error("booking not found", 404)
        return jsonify(booking_dict(row))

    @app.delete("/bookings/<booking_id>")
    def cancel_booking(booking_id):
        db = get_db(app)
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
            if not row:
                db.rollback()
                return error("booking not found", 404)
            if row["status"] == "cancelled":
                db.rollback()
                return error("booking already cancelled", 409)
            db.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))
            updated = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
            db.commit()
            return jsonify(booking_dict(updated))
        except Exception:
            db.rollback()
            raise

    return app

def booking_dict(row):
    return {
        "id": row["id"], "slot_id": row["slot_id"], "name": row["name"],
        "email": row["email"], "status": row["status"], "created_at": row["created_at"]
    }

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=os.environ.get("FLASK_DEBUG") == "1")
