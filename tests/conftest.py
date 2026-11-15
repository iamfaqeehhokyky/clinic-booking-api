import pytest
from app import create_app

@pytest.fixture()
def app(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    return create_app({
        "TESTING": True,
        "DATABASE": str(db_path),
        "API_KEY": "test-key",
        "RATE_LIMIT": 5,
        "RATE_WINDOW_SECONDS": 60,
    })

@pytest.fixture()
def client(app):
    return app.test_client()

@pytest.fixture()
def headers():
    return {"X-API-Key": "test-key"}

@pytest.fixture()
def future_slot():
    from datetime import datetime, timezone, timedelta
    start = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
    end = start + timedelta(minutes=30)
    return {"id":"s1","provider":"Dr Ade","start":start.isoformat().replace("+00:00","Z"),
            "end":end.isoformat().replace("+00:00","Z")}

@pytest.fixture()
def seed(client, headers, future_slot):
    response = client.post("/reset", json={"slots":[future_slot]}, headers=headers)
    assert response.status_code == 200
    return future_slot
