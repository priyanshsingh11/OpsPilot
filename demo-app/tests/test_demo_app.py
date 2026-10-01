import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.delenv("SIMULATE_CALENDAR_FAILURE", raising=False)
    from main import app

    with TestClient(app, follow_redirects=False) as c:
        yield c


def rows(sql, *args):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def schedule(client, cid=1, who="Vikram Desai", date="2026-10-02", start="10:00", dur=45):
    return client.post(
        f"/candidates/{cid}/interviews",
        data={"interviewer": who, "date": date, "start_time": start, "duration_minutes": dur},
    )


def test_seed_and_pages(client):
    assert len(rows("SELECT * FROM jobs")) == 2
    assert {r["status"] for r in rows("SELECT status FROM candidates")} == set(db.STATUSES)
    for path in ("/", "/jobs/1", "/candidates/1", "/interviews", "/admin"):
        assert client.get(path).status_code == 200
    assert client.get("/jobs/99").status_code == 404


def test_filter_by_status(client):
    html = client.get("/jobs/1?status=Shortlisted").text
    assert 'data-testid="candidate-row-1"' in html and 'data-testid="candidate-row-2"' in html
    assert 'data-testid="candidate-row-3"' not in html  # Ankit is Screening
    assert "3 candidate(s)" in html
    assert "No candidates match" in client.get("/jobs/2?status=Rejected").text


def test_update_status(client):
    r = client.post("/candidates/3/status", data={"status": "Shortlisted"})
    assert r.status_code == 303
    assert rows("SELECT status FROM candidates WHERE id=3")[0]["status"] == "Shortlisted"
    assert client.post("/candidates/3/status", data={"status": "Bogus"}).status_code == 400


def test_create_interview_and_rules(client):
    assert schedule(client).status_code == 303
    iv = rows("SELECT * FROM interviews WHERE candidate_id=1")
    assert len(iv) == 1 and iv[0]["start_time"] == "10:00" and iv[0]["status"] == "scheduled"
    # scheduling does not change candidate status on its own
    assert rows("SELECT status FROM candidates WHERE id=1")[0]["status"] == "Shortlisted"
    # duplicate for same candidate
    assert schedule(client, start="15:00").status_code == 409
    # interviewer overlap (Vikram 10:00-10:45)
    assert schedule(client, cid=2, start="10:30").status_code == 409
    # seeded Anita 14:00-15:00 conflicts
    assert schedule(client, cid=2, who="Anita Rao", start="14:30").status_code == 409
    # outside working hours, rejected candidate
    assert schedule(client, cid=2, start="17:30", dur=60).status_code == 400
    assert schedule(client, cid=5, start="11:00").status_code == 409
    assert len(rows("SELECT * FROM interviews")) == 2  # seed + one new


def test_failure_before_write_then_safe_retry(client):
    client.post("/admin/failure", data={"remaining": 1, "mode": "before_write"})
    r = schedule(client)
    assert r.status_code == 503 and 'data-testid="error-banner"' in r.text
    assert rows("SELECT * FROM interviews WHERE candidate_id=1") == []  # nothing created
    assert schedule(client).status_code == 303  # failure was one-shot; retry works
    assert len(rows("SELECT * FROM interviews WHERE candidate_id=1")) == 1


def test_failure_after_write_creates_row_and_blocks_duplicate(client):
    client.post("/admin/failure", data={"remaining": 1, "mode": "after_write"})
    assert schedule(client).status_code == 503
    assert len(rows("SELECT * FROM interviews WHERE candidate_id=1")) == 1  # created despite error
    assert schedule(client).status_code == 409  # blind retry is rejected, no duplicate
    assert len(rows("SELECT * FROM interviews WHERE candidate_id=1")) == 1


def test_validation_error_does_not_consume_failure(client):
    client.post("/admin/failure", data={"remaining": 1, "mode": "before_write"})
    assert schedule(client, start="17:30", dur=60).status_code == 400
    assert client.get("/api/state").json()["failure"]["remaining"] == 1


def test_env_arms_failure_on_reset(client, monkeypatch):
    monkeypatch.setenv("SIMULATE_CALENDAR_FAILURE", "true")
    monkeypatch.setenv("CALENDAR_FAILURE_MODE", "after_write")
    client.post("/admin/reset")
    assert client.get("/api/state").json()["failure"] == {"remaining": 1, "mode": "after_write"}
    assert schedule(client).status_code == 503


def test_cancel_and_reset(client):
    schedule(client)
    iid = rows("SELECT id FROM interviews WHERE candidate_id=1")[0]["id"]
    client.post(f"/interviews/{iid}/cancel")
    assert rows("SELECT status FROM interviews WHERE id=?", iid)[0]["status"] == "cancelled"
    assert schedule(client).status_code == 303  # cancelled slot no longer blocks
    client.post("/admin/reset")
    assert len(rows("SELECT * FROM interviews")) == 1
