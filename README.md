# Clinic Booking API

A Flask + SQLite implementation of the Koya Academy Clinic Booking API assessment. It implements the supplied OpenAPI contract and a small patient-facing web page.

## Features by assessment level

1. **Level 1:** `GET /slots`, `POST /bookings`, `GET /bookings/{id}`, and `POST /reset`.
2. **Level 2:** `X-API-Key` authentication; required-field, email, slot-existence, and already-started validation.
3. **Level 3:** SQLite `BEGIN IMMEDIATE` write transactions plus a partial unique index prevent simultaneous confirmed bookings for one slot. `DELETE /bookings/{id}` cancels and frees the slot.
4. **Level 4:** `Idempotency-Key` replay protection and cursor-based patient booking history.
5. **Level 5:** Five booking attempts per normalized email in a rolling 60-second window. Failed attempts count; the sixth receives `429` and `Retry-After`.

The browser page is at `/`. It supports viewing slots, booking, viewing booking history, loading additional pages, and cancelling a booking.

## Requirements

- Python 3.10 or newer
- pip

## Local setup

### 1. Create and activate a virtual environment

Windows PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env`, then choose a long random API key. Example:

```env
API_KEY=replace-with-a-preferable-long-random-secret-key
DATABASE_PATH=instance/clinic.sqlite3
FLASK_DEBUG=1
PORT=5000
```

The app does not automatically load `.env`; export the variables in your shell, or install `python-dotenv` and add `load_dotenv()` at the top of `app.py`.

PowerShell example:

```powershell
$env:API_KEY="replace-with-a-preferable-long-random-secret-key"
$env:DATABASE_PATH="instance/clinic.sqlite3"
$env:FLASK_DEBUG="1"
```

macOS / Linux:

```bash
export API_KEY="replace-with-a-preferable-long-random-secret-key"
export DATABASE_PATH="instance/clinic.sqlite3"
export FLASK_DEBUG=1
```

### 4. Start the server

```bash
python app.py
```

Open:

- Patient booking page: http://127.0.0.1:5000/
- API slots endpoint: http://127.0.0.1:5000/slots

Enter the same API key configured in the server into the page's API key field.

## Initialize sample appointment slots

The assessment contract does not prescribe initial sample data. Use the protected reset endpoint to add slots:

```bash
curl -X POST http://127.0.0.1:5000/reset \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-secret-change-me" \
  -d '{
    "slots": [
      {
        "id": "s1",
        "provider": "Dr Nurudeen",
        "start": "2026-12-01T09:00:00Z",
        "end": "2026-12-01T09:30:00Z"
      },
      {
        "id": "s2",
        "provider": "Dr Fatimah",
        "start": "2026-12-01T10:00:00Z",
        "end": "2026-12-01T10:30:00Z"
      },
      {
        "id": "s3",
        "provider": "Dr Kafayah",
        "start": "2026-12-01T10:00:00Z",
        "end": "2026-12-01T10:30:00Z"
      },
      {
        "id": "s4",
        "provider": "Dr Uthman",
        "start": "2026-12-01T10:00:00Z",
        "end": "2026-12-01T10:30:00Z"
      },
      {
        "id": "s5",
        "provider": "Dr Zakariyyah",
        "start": "2026-12-01T10:00:00Z",
        "end": "2026-12-01T10:30:00Z"
      },
      {
        "id": "s6",
        "provider": "Dr Kenny",
        "start": "2026-12-01T10:00:00Z",
        "end": "2026-12-01T10:30:00Z"
      },
      {
        "id": "s7",
        "provider": "Dr Adedayo",
        "start": "2026-12-01T11:00:00Z",
        "end": "2026-12-01T11:30:00Z"
      }
    ]
  }'
```

Use future appointment times. `/reset` intentionally deletes all existing bookings and replaces the slot list, as specified by the API contract.

## API examples

All API calls require `X-API-Key`.

### List slots

```bash
curl http://127.0.0.1:5000/slots \
  -H "X-API-Key: dev-secret-change-me"
```

Optional exact provider filter:

```bash
curl "http://127.0.0.1:5000/slots?provider=Dr%20Adedayo" \
  -H "X-API-Key: dev-secret-change-me"
```

### Book an appointment

```bash
curl -X POST http://127.0.0.1:5000/bookings \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-secret-change-me" \
  -H "Idempotency-Key: patient-request-001" \
  -d '{"slot_id":"s1","name":"Sulaimon Olanrewaju","email":"sulaimon@domain.com"}'
```

A successful response is HTTP `201` and contains `id`, `slot_id`, `name`, `email`, `status`, and `created_at`.

### Retrieve a booking

```bash
curl http://127.0.0.1:5000/bookings/BOOKING_ID \
  -H "X-API-Key: dev-secret-change-me"
```

### List a patient's bookings

```bash
curl "http://127.0.0.1:5000/bookings?email=sulaimon%40domain.com&limit=10" \
  -H "X-API-Key: dev-secret-change-me"
```

Pass the returned `next_cursor` as `cursor` to retrieve the next page.

### Cancel a booking

```bash
curl -X DELETE http://127.0.0.1:5000/bookings/BOOKING_ID \
  -H "X-API-Key: dev-secret-change-me"
```

## Automated tests

Run all five assessment-level test groups:

```bash
python -m pytest -v
```

The test suite covers:

- Level 1: slot listing, successful booking, booking lookup.
- Level 2: missing/wrong API key, required fields, email validation, missing slot, past slot.
- Level 3: concurrent booking race, cancellation, slot becoming available again, repeated cancellation.
- Level 4: idempotent replay, key/body mismatch, cursor pagination, case-insensitive email matching.
- Level 5: five attempts allowed, failed attempts counted, sixth attempt throttled, independent email quota.

Tests use a temporary SQLite database and do not modify your local clinic database.

## Deployment checklist

1. Use a production WSGI server such as Gunicorn (Linux) or Waitress (Windows).
2. Set a strong, unique `API_KEY`; never deploy with the development key.
3. Set `DATABASE_PATH` to a persistent writable disk location.
4. Use HTTPS.
5. Keep the SQLite database on persistent storage. Do not use an ephemeral serverless filesystem for this database.
6. Back up the database regularly.
7. Do not expose `/reset` to ordinary patients. It is API-key protected, but in a real deployment it should also be restricted to an administrator-only credential or removed from the public application.
8. The API key is necessarily entered into the demo browser page and can be inspected by that browser user. For a public production frontend, put the privileged key behind a trusted backend/session-authentication layer rather than shipping it to every visitor.
9. Run `pytest -v` before deployment.

Example production server command after installing Gunicorn:

```bash
pip install gunicorn
gunicorn --workers 1 --bind 0.0.0.0:8000 "app:app"
```

SQLite serializes write transactions. For a single-instance deployment on persistent local storage this is appropriate for this assessment. For multiple app instances or serverless deployment, use a shared transactional database such as PostgreSQL instead of a local SQLite file.

## Notes on implementation decisions

- Timestamps must be ISO 8601 date-times with a timezone. They are stored normalized to UTC.
- Names are trimmed; emails are trimmed and lowercased.
- The same idempotency key and same JSON body replay the original status and response body. Reusing it with a different JSON body returns `422`.
- Idempotency replays are checked before rate limiting so a legitimate retry gets the original response.
- The rate limiter uses a SQLite table, so its state persists across requests and application restarts.
- The frontend creates a fresh idempotency key for each intentional booking submission.
