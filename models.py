from sqlalchemy import Index, text
from database import db


class Slot(db.Model):
    __tablename__ = "slots"

    id = db.Column(db.String(128), primary_key=True)
    provider = db.Column(db.String(255), nullable=False)
    start = db.Column(db.String(40), nullable=False)
    end = db.Column(db.String(40), nullable=False)

    bookings = db.relationship(
        "Booking",
        back_populates="slot",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Booking(db.Model):
    __tablename__ = "bookings"

    id = db.Column(db.String(36), primary_key=True)
    slot_id = db.Column(
        db.String(128),
        db.ForeignKey("slots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(320), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.String(40), nullable=False)

    slot = db.relationship("Slot", back_populates="bookings")

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('confirmed', 'cancelled')",
            name="ck_bookings_status",
        ),
        Index("bookings_email_order", "email", "created_at", "id"),
        # A slot can have many historical cancelled bookings, but only one
        # currently confirmed booking. Supported by PostgreSQL and SQLite.
        Index(
            "one_confirmed_booking_per_slot",
            "slot_id",
            unique=True,
            postgresql_where=text("status = 'confirmed'"),
            sqlite_where=text("status = 'confirmed'"),
        ),
    )


class IdempotencyRecord(db.Model):
    __tablename__ = "idempotency"

    idem_key = db.Column(db.String(255), primary_key=True)
    body_hash = db.Column(db.String(64), nullable=False)
    status_code = db.Column(db.Integer, nullable=False)
    response_body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.String(40), nullable=False)


class RateLimitAttempt(db.Model):
    __tablename__ = "rate_attempts"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    email = db.Column(db.String(320), nullable=False, index=True)
    attempted_at = db.Column(db.Float, nullable=False)

    __table_args__ = (
        Index("rate_attempts_email_time", "email", "attempted_at"),
    )
