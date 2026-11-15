import hashlib
import json
import os
import uuid
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from email_validator import validate_email, EmailNotValidError
from flask import Flask, jsonify, request, render_template
from sqlalchemy import select, delete, func, text, update
from sqlalchemy.exc import IntegrityError

from database import (
    db, init_db, get_db, utc_now, parse_datetime, canonical_json,
    normalize_database_url,
)
from models import Slot, Booking, IdempotencyRecord, RateLimitAttempt

load_dotenv()


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    default_db = "sqlite:///" + os.path.join(app.instance_path, "clinic.sqlite3").replace("\\", "/")
    database_url = normalize_database_url(os.environ.get("DATABASE_URL", default_db))

    app.config.update(
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        API_KEY=os.environ.get("API_KEY", "dev-secret-change-me"),
        RATE_LIMIT=5,
        RATE_WINDOW_SECONDS=60,
    )
    if test_config:
        app.config.update(test_config)
        if app.config.get("DATABASE"):
            app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + str(app.config["DATABASE"]).replace("\\", "/")
        if app.config.get("DATABASE_URL"):
            app.config["SQLALCHEMY_DATABASE_URI"] = normalize_database_url(app.config["DATABASE_URL"])

    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///"):
        os.makedirs(app.instance_path, exist_ok=True)

    init_db(app)

    def error(message, status):
        return jsonify({"error": message}), status

    @app.before_request
    def require_api_key():
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

        try:
            # Delete dependent records first so reset works on both databases.
            db.session.execute(delete(Booking))
            db.session.execute(delete(Slot))
            db.session.execute(delete(IdempotencyRecord))
            db.session.add_all([
                Slot(id=sid, provider=provider, start=start, end=end)
                for sid, provider, start, end in normalized
            ])
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return jsonify({"message": "slots replaced", "count": len(normalized)}), 200

    @app.get("/slots")
    def list_slots():
        provider = request.args.get("provider")
        query = (
            db.session.query(
                Slot,
                (
                    ~db.session.query(Booking.id)
                    .filter(Booking.slot_id == Slot.id, Booking.status == "confirmed")
                    .exists()
                ).label("available"),
            )
        )
        if provider is not None:
            query = query.filter(Slot.provider == provider)
        rows = query.order_by(Slot.start.asc(), Slot.id.asc()).all()
        return jsonify({"slots": [
            {
                "id": slot.id,
                "provider": slot.provider,
                "start": slot.start,
                "end": slot.end,
                "available": bool(available),
            }
            for slot, available in rows
        ]})

    def email_for_rate_limit(payload):
        if isinstance(payload, dict) and isinstance(payload.get("email"), str):
            return payload["email"].strip().lower()[:320]
        return None

    def record_rate_attempt(email):
        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        cutoff = now_ts - app.config["RATE_WINDOW_SECONDS"]

        # PostgreSQL transaction-scoped advisory lock serializes attempts for
        # one email, including the first attempt when no row exists yet.
        if db.engine.dialect.name == "postgresql":
            db.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:email, 0))"),
                {"email": email},
            )

        db.session.execute(
            delete(RateLimitAttempt).where(RateLimitAttempt.attempted_at < cutoff)
        )
        attempts = db.session.query(func.count(RateLimitAttempt.id)).filter(
            RateLimitAttempt.email == email,
            RateLimitAttempt.attempted_at >= cutoff,
        ).scalar()

        if attempts >= app.config["RATE_LIMIT"]:
            oldest = db.session.query(func.min(RateLimitAttempt.attempted_at)).filter(
                RateLimitAttempt.email == email,
                RateLimitAttempt.attempted_at >= cutoff,
            ).scalar()
            retry_after = max(
                1,
                int(oldest + app.config["RATE_WINDOW_SECONDS"] - now_ts + 0.999),
            )
            db.session.commit()
            return retry_after

        db.session.add(RateLimitAttempt(email=email, attempted_at=now_ts))
        db.session.commit()
        return None

    def save_idempotency(idem_key, body_hash, status_code, response_body):
        if not idem_key:
            return
        db.session.add(IdempotencyRecord(
            idem_key=idem_key,
            body_hash=body_hash,
            status_code=status_code,
            response_body=response_body,
            created_at=utc_now(),
        ))
        db.session.commit()

    @app.post("/bookings")
    def create_booking():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error("request body must be a JSON object", 400)

        idem_key = request.headers.get("Idempotency-Key")
        body_canonical = canonical_json(payload)
        body_hash = hashlib.sha256(body_canonical.encode("utf-8")).hexdigest()

        if idem_key:
            existing = db.session.get(IdempotencyRecord, idem_key)
            if existing:
                if existing.body_hash != body_hash:
                    return error("Idempotency-Key was already used with a different request body", 422)
                return app.response_class(
                    existing.response_body,
                    status=existing.status_code,
                    mimetype="application/json",
                )

        rate_email = email_for_rate_limit(payload)
        if rate_email:
            try:
                retry_after = record_rate_attempt(rate_email)
            except Exception:
                db.session.rollback()
                raise
            if retry_after is not None:
                response, status = error("too many booking attempts; try again later", 429)
                response.headers["Retry-After"] = str(retry_after)
                return response, status

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

        result = None
        response_body = None
        try:
            # Acquire a write lock on the slot before checking availability.
            # This serializes competing bookings on SQLite and locks the row
            # on PostgreSQL. The partial unique index remains the final guard.
            db.session.execute(
                update(Slot).where(Slot.id == slot_id).values(id=Slot.id)
            )
            slot = db.session.get(Slot, slot_id)
            if not slot:
                result = ({"error": "slot not found"}, 404)
            else:
                start = parse_datetime(slot.start)
                if start <= datetime.now(timezone.utc):
                    result = ({"error": "slot has already started"}, 400)
                else:
                    occupied = db.session.query(Booking.id).filter(
                        Booking.slot_id == slot_id,
                        Booking.status == "confirmed",
                    ).first()
                    if occupied:
                        result = ({"error": "slot already booked"}, 409)
                    else:
                        booking = Booking(
                            id=str(uuid.uuid4()),
                            slot_id=slot_id,
                            name=name,
                            email=email,
                            status="confirmed",
                            created_at=utc_now(),
                        )
                        db.session.add(booking)
                        db.session.flush()
                        result = ({
                            "id": booking.id,
                            "slot_id": booking.slot_id,
                            "name": booking.name,
                            "email": booking.email,
                            "status": booking.status,
                            "created_at": booking.created_at,
                        }, 201)

            response_body = json.dumps(result[0], separators=(",", ":"))
            db.session.commit()
        except IntegrityError:
            # The partial unique index is the final guard against two requests
            # confirming the same slot at the same time.
            db.session.rollback()
            result = ({"error": "slot already booked"}, 409)
            response_body = json.dumps(result[0], separators=(",", ":"))
        except Exception:
            db.session.rollback()
            raise

        if idem_key:
            try:
                save_idempotency(idem_key, body_hash, result[1], response_body)
            except IntegrityError:
                db.session.rollback()
                existing = db.session.get(IdempotencyRecord, idem_key)
                if existing and existing.body_hash == body_hash:
                    return app.response_class(
                        existing.response_body,
                        status=existing.status_code,
                        mimetype="application/json",
                    )
                return error("Idempotency-Key was already used with a different request body", 422)

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

        email = email.strip().lower()
        try:
            limit = int(request.args.get("limit", "10"))
        except (TypeError, ValueError):
            return error("limit must be between 1 and 50", 400)
        if not 1 <= limit <= 50:
            return error("limit must be between 1 and 50", 400)

        cursor = request.args.get("cursor")
        query = Booking.query.filter(func.lower(Booking.email) == email)
        if cursor:
            cursor_row = Booking.query.filter(
                Booking.id == cursor,
                func.lower(Booking.email) == email,
            ).first()
            if not cursor_row:
                return error("invalid cursor", 400)
            query = query.filter(
                (Booking.created_at > cursor_row.created_at)
                | (
                    (Booking.created_at == cursor_row.created_at)
                    & (Booking.id > cursor_row.id)
                )
            )

        rows = query.order_by(
            Booking.created_at.asc(), Booking.id.asc()
        ).limit(limit + 1).all()

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = page[-1].id if has_more and page else None
        return jsonify({
            "bookings": [booking_dict(row) for row in page],
            "next_cursor": next_cursor,
        })

    @app.get("/bookings/<booking_id>")
    def get_booking(booking_id):
        row = db.session.get(Booking, booking_id)
        if not row:
            return error("booking not found", 404)
        return jsonify(booking_dict(row))

    @app.delete("/bookings/<booking_id>")
    def cancel_booking(booking_id):
        # Lock the booking row before reading/updating its status. This avoids
        # two concurrent cancellation requests both reporting success.
        db.session.execute(
            update(Booking).where(Booking.id == booking_id).values(status=Booking.status)
        )
        row = db.session.get(Booking, booking_id)
        if not row:
            return error("booking not found", 404)
        if row.status == "cancelled":
            return error("booking already cancelled", 409)
        row.status = "cancelled"
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return jsonify(booking_dict(row))

    return app


def booking_dict(row):
    return {
        "id": row.id,
        "slot_id": row.slot_id,
        "name": row.name,
        "email": row.email,
        "status": row.status,
        "created_at": row.created_at,
    }


app = create_app()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
