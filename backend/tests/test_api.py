"""API: start a run in the background, poll it, and read back the persisted event log."""

import time

import pytest
from fastapi.testclient import TestClient

from app.agent.driver import BrowserDriver
from app.agent.runner import AgentOptions


@pytest.fixture()
def api(demo_app_url, tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import database, main
    from app.agent import manager
    from app.agent.driver import HarnessClient

    # Isolated SQLite file for this test.
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(manager, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))

    def tools():  # a fresh browser in the run's own thread, like production
        return (BrowserDriver.launch(demo_app_url, screenshot_dir=str(tmp_path)),
                HarnessClient.connect(demo_app_url))

    monkeypatch.setattr(main, "runs", manager.RunManager(tools, AgentOptions(retry_backoff_seconds=0)))
    with TestClient(main.app) as client:
        yield client


def wait_for(client, run_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_run_via_api_with_recovery(api):
    assert {s["key"] for s in api.get("/api/scenarios").json()} == {
        "none", "transient", "saved_but_failed", "outage"}
    r = api.post("/api/runs", json={
        "goal": "Schedule a Technical Screen for Diya Patel with Priya Nair on 2026-10-08 at 11:00",
        "scenario": "saved_but_failed", "reset_demo_data": True})
    assert r.status_code == 202
    run = wait_for(api, r.json()["id"])
    assert run["status"] == "completed"
    types = [e["type"] for e in run["events"]]
    assert "ACTION_FAILED" in types and "RETRY" not in types and types[-1] == "COMPLETED"
    assert api.get("/api/runs").json()[0]["id"] == run["id"]


def test_unknown_scenario_rejected(api):
    assert api.post("/api/runs", json={"goal": "Schedule x", "scenario": "nope"}).status_code == 400
