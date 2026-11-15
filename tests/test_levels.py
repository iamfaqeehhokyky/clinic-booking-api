import threading
from concurrent.futures import ThreadPoolExecutor

def book(client, headers, slot="s1", email="ada@example.com", name="Ada Obi", **extra):
    body = {"slot_id":slot,"name":name,"email":email}
    body.update(extra)
    return client.post("/bookings", json=body, headers=headers)

def test_level_1_slots_book_and_lookup(client, headers, seed):
    slots = client.get("/slots", headers=headers).get_json()["slots"]
    assert len(slots) == 1 and slots[0]["available"] is True
    response = book(client, headers)
    assert response.status_code == 201
    booking = response.get_json()
    assert booking["status"] == "confirmed"
    lookup = client.get(f"/bookings/{booking['id']}", headers=headers)
    assert lookup.status_code == 200
    assert lookup.get_json()["id"] == booking["id"]

def test_level_2_api_key_and_validation(client, headers, seed):
    assert client.get("/slots").status_code == 401
    assert client.get("/slots", headers={"X-API-Key":"wrong"}).status_code == 401
    assert book(client, headers, name=" ").status_code == 400
    assert book(client, headers, email="not-an-email").status_code == 400
    assert book(client, headers, slot="missing").status_code == 404
    assert book(client, headers).status_code == 201
    # A slot that has already started must be rejected.
    from datetime import datetime, timezone, timedelta
    start = (datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat().replace("+00:00","Z")
    end = (datetime.now(timezone.utc)+timedelta(minutes=28)).isoformat().replace("+00:00","Z")
    client.post("/reset", json={"slots":[{"id":"past","provider":"Dr A","start":start,"end":end}]}, headers=headers)
    assert book(client, headers, slot="past", email="other@example.com").status_code == 400

def test_level_3_cancel_and_concurrent_double_booking(client, headers, seed):
    # Use independent Flask clients to simulate requests arriving concurrently.
    app = client.application
    barrier = threading.Barrier(2)
    def attempt(email):
        with app.test_client() as c:
            barrier.wait()
            return c.post("/bookings", json={"slot_id":"s1","name":"Patient","email":email},
                          headers=headers).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(attempt, ["one@example.com","two@example.com"]))
    assert sorted(statuses) == [201,409]
    booking = client.get("/bookings", query_string={"email":"one@example.com"}, headers=headers).get_json()["bookings"]
    if not booking:
        booking = client.get("/bookings", query_string={"email":"two@example.com"}, headers=headers).get_json()["bookings"]
    booking_id = booking[0]["id"]
    cancelled = client.delete(f"/bookings/{booking_id}", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.get_json()["status"] == "cancelled"
    assert client.delete(f"/bookings/{booking_id}", headers=headers).status_code == 409
    assert client.get("/slots", headers=headers).get_json()["slots"][0]["available"] is True

def test_level_4_idempotency_and_pagination(client, headers, future_slot):
    # Seed multiple slots to allow multiple bookings by the same patient.
    slots = []
    from datetime import datetime, timezone, timedelta
    start = datetime.now(timezone.utc) + timedelta(days=4)
    for i in range(3):
        s = start + timedelta(hours=i)
        slots.append({"id":f"s{i}","provider":"Dr Ade","start":s.isoformat().replace("+00:00","Z"),
                      "end":(s+timedelta(minutes=30)).isoformat().replace("+00:00","Z")})
    client.post("/reset", json={"slots":slots}, headers=headers)
    body = {"slot_id":"s0","name":"Ada Obi","email":"ADA@example.com"}
    first = client.post("/bookings", json=body, headers={**headers,"Idempotency-Key":"retry-1"})
    again = client.post("/bookings", json=body, headers={**headers,"Idempotency-Key":"retry-1"})
    assert first.status_code == again.status_code == 201
    assert first.get_json()["id"] == again.get_json()["id"]
    changed = client.post("/bookings", json={**body,"name":"Different"}, headers={**headers,"Idempotency-Key":"retry-1"})
    assert changed.status_code == 422
    for i in (1,2):
        assert client.post("/bookings", json={**body,"slot_id":f"s{i}"}, headers=headers).status_code == 201
    page1 = client.get("/bookings?email=ada@example.com&limit=2", headers=headers).get_json()
    assert len(page1["bookings"]) == 2 and page1["next_cursor"]
    page2 = client.get("/bookings", query_string={"email":"ADA@example.com","limit":2,"cursor":page1["next_cursor"]},
                       headers=headers).get_json()
    assert len(page2["bookings"]) == 1 and page2["next_cursor"] is None

def test_level_5_rate_limit_includes_failed_attempts(client, headers, seed):
    statuses = []
    for i in range(6):
        response = book(client, headers, slot="does-not-exist", email="spam@example.com")
        statuses.append(response.status_code)
        if i == 5:
            assert response.headers.get("Retry-After") is not None
    assert statuses == [404,404,404,404,404,429]
    # A different email has an independent allowance.
    assert book(client, headers, slot="does-not-exist", email="different@example.com").status_code == 404
