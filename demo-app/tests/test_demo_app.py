"""The synthetic recruitment app on its own (no browser): data, filtering, scheduling, status,
invitations, and the deterministic failure simulation the agent's recovery is tested against."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.chdir(APP_DIR)  # the app loads templates/ and static/ relative to its own folder
    monkeypatch.delenv("DEMO_FAILING_ATTEMPTS", raising=False)
    from app.main import app
    from app.store import store

    store.reset()
    with TestClient(app, follow_redirects=False) as c:
        yield c
    store.reset()


def state(client):
    return client.get("/api/state").json()


def book(client, cid="cand-aarav", when="2026-10-08T10:00", who="Priya Nair", round_name="Technical Screen"):
    return client.post(f"/candidates/{cid}/interviews",
                       data={"round_name": round_name, "scheduled_at": when, "interviewer": who})


def test_seed_data_and_pages(client):
    s = state(client)
    assert {c["status"] for c in s["candidates"]} >= {"applied", "screening", "shortlisted", "interview", "rejected"}
    for path in ("/jobs", "/jobs/job-ai-engineer", "/candidates/cand-aarav", "/interviews",
                 "/candidates/cand-aarav/interviews/new"):
        assert client.get(path).status_code == 200
    assert client.get("/jobs/nope").status_code == 404
    assert client.get("/candidates/nope").status_code == 404


def test_filter_candidates_by_status(client):
    html = client.get("/jobs/job-ai-engineer?status=shortlisted").text
    assert "Aarav Sharma" in html and "Diya Patel" in html
    assert "Rohan Gupta" not in html and "Kabir Singh" not in html  # applied / rejected


def test_create_interview_and_update_status(client):
    assert book(client).status_code == 303
    created = [i for i in state(client)["interviews"] if i["candidate_id"] == "cand-aarav"]
    assert len(created) == 1 and created[0]["round"] == "Technical Screen" and created[0]["status"] == "scheduled"
    assert client.post("/candidates/cand-aarav/status", data={"status": "interview"}).status_code == 303
    assert next(c for c in state(client)["candidates"] if c["id"] == "cand-aarav")["status"] == "interview"
    assert client.post("/candidates/cand-aarav/status", data={"status": "bogus"}).status_code == 400
    assert book(client, when="not a date").status_code == 400


def test_before_write_failure_saves_nothing_and_is_one_shot(client):
    client.post("/admin/failure", data={"remaining": "1", "mode": "before_write"})
    r = book(client)
    assert r.status_code == 500 and 'data-testid="error-banner"' in r.text
    assert [i for i in state(client)["interviews"] if i["candidate_id"] == "cand-aarav"] == []
    assert book(client).status_code == 303  # the plan is used up; the retry works


def test_after_write_failure_saves_the_interview_anyway(client):
    """The ambiguous case the agent must handle: an error was returned, but the record exists."""
    client.post("/admin/failure", data={"remaining": "1", "mode": "after_write"})
    assert book(client).status_code == 500
    assert len([i for i in state(client)["interviews"] if i["candidate_id"] == "cand-aarav"]) == 1


def test_app_does_not_dedupe_so_the_agent_must(client):
    book(client); book(client)
    assert len([i for i in state(client)["interviews"] if i["candidate_id"] == "cand-aarav"]) == 2


def test_failure_plan_can_be_armed_from_the_environment(client, monkeypatch):
    monkeypatch.setenv("DEMO_FAILING_ATTEMPTS", "2")
    monkeypatch.setenv("DEMO_FAILURE_MODE", "after_write")
    client.post("/admin/reset")
    assert state(client)["failure"] == {"chaos_mode": False, "remaining": 2, "mode": "after_write"}


def test_invitations(client):
    book(client)
    iid = next(i["id"] for i in state(client)["interviews"] if i["candidate_id"] == "cand-aarav")
    ok = client.post("/candidates/cand-aarav/invitations",
                     data={"interview_id": iid, "subject": "Hello", "message": "a\r\nb"})
    assert ok.status_code == 303
    sent = state(client)["invitations"]
    assert len(sent) == 1 and sent[0]["to"] == "aarav.sharma@example.com" and sent[0]["message"] == "a\nb"
    # Someone else's interview, and a blank subject, are refused and send nothing.
    assert client.post("/candidates/cand-diya/invitations",
                       data={"interview_id": iid, "subject": "x", "message": "y"}).status_code == 400
    assert client.post("/candidates/cand-aarav/invitations",
                       data={"interview_id": iid, "subject": " ", "message": "y"}).status_code == 400
    assert len(state(client)["invitations"]) == 1


def test_reset_restores_seed_data(client):
    book(client)
    client.post("/admin/reset")
    assert [i for i in state(client)["interviews"] if i["candidate_id"] == "cand-aarav"] == []
