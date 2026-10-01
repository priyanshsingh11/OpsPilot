"""API: start a run in the background, poll it, and read back the persisted event log."""

import time

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
        "none", "transient", "saved_but_failed", "outage", "partial_outage"}
    r = api.post("/api/runs", json={
        "goal": "Schedule a Technical Screen for Diya Patel with Priya Nair on 2026-10-08 at 11:00. Do not send an invitation.",
        "scenario": "saved_but_failed", "reset_demo_data": True})
    assert r.status_code == 202
    run = wait_for(api, r.json()["id"])
    assert run["status"] == "completed"
    types = [e["type"] for e in run["events"]]
    assert "ACTION_FAILED" in types and "RETRY" not in types and types[-1] == "COMPLETED"
    assert api.get("/api/runs").json()[0]["id"] == run["id"]


def test_unknown_scenario_rejected(api):
    assert api.post("/api/runs", json={"goal": "Schedule x", "scenario": "nope"}).status_code == 400
