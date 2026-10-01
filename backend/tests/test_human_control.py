"""Phase 6: human control. Approval before sending, reject, pause, stop, no bypass, safe resume.

Everything runs for real: the FastAPI app, a background agent thread, a Chromium browser, and
the demo recruitment app. State is checked in the demo app's own records.
"""

import time
from datetime import date

import pytest

from app.agent import events as ev
from app.agent.control import ApprovalGrant, ApprovalRequired, RunControl, payload_hash
from app.agent.goal import parse_interview_goal
from app.agent.manager import RunManager

GOAL = "Schedule a Technical Screen for Aarav Sharma with Priya Nair on 2026-10-08 at 10:00"
AARAV = "cand-aarav"


def start(client, goal=GOAL, reset=True, scenario="none"):
    r = client.post("/api/runs", json={"goal": goal, "scenario": scenario, "reset_demo_data": reset})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def wait_status(client, run_id, *statuses, timeout=30):
    deadline = time.monotonic() + timeout
    run = None
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.1)
    raise AssertionError(f"run stayed '{run and run['status']}', wanted {statuses}")


def app_state(harness):
    return harness.source_of_truth()


def invitations(harness):
    return app_state(harness)["invitations"]


def post(client, run_id, path):
    return client.post(f"/api/runs/{run_id}/{path}")


def types(run):
    return [e["type"] for e in run["events"]]


def test_pauses_for_approval_then_sends_after_approve(api, harness):
    run_id = start(api)
    run = wait_status(api, run_id, "awaiting_approval")

    # Safe actions are done, the high-impact one is not.
    assert invitations(harness) == []
    state = app_state(harness)
    assert any(i["candidate_id"] == AARAV and i["round"] == "Technical Screen" for i in state["interviews"])
    assert next(c for c in state["candidates"] if c["id"] == AARAV)["status"] == "interview"

    # The approval request tells the user what, who, what is sent, and what approving does.
    pending = run["pending_approval"]
    d = pending["details"]
    assert d["title"].startswith("Approval Required")
    assert d["candidates"][0]["name"] == "Aarav Sharma" and d["candidates"][0]["email"]
    assert d["email"]["to"] == "aarav.sharma@example.com"
    assert "Technical Screen" in d["email"]["subject"] and "Priya Nair" in d["email"]["message"]
    assert d["if_approved"] and d["if_rejected"]
    assert types(run)[-1] == ev.APPROVAL_REQUESTED
    assert run["checkpoint"]["steps"] == {"schedule_interview": "done", "update_status": "done",
                                          "send_invitation": "awaiting_approval"}
    time.sleep(0.6)
    assert invitations(harness) == []  # still waiting: it does not time out into sending

    assert post(api, run_id, f"approvals/{pending['id']}/approve").status_code == 200
    run = wait_status(api, run_id, "completed")

    sent = invitations(harness)
    assert len(sent) == 1
    assert (sent[0]["to"], sent[0]["subject"]) == (d["email"]["to"], d["email"]["subject"])
    assert sent[0]["message"] == d["email"]["message"]  # exactly what the user approved
    t = types(run)
    assert [x for x in t if x in (ev.APPROVAL_REQUESTED, ev.APPROVAL_GRANTED, ev.COMPLETED)] == [
        ev.APPROVAL_REQUESTED, ev.APPROVAL_GRANTED, ev.COMPLETED]
    assert t.index(ev.APPROVAL_GRANTED) < t.index(ev.ACTION, t.index(ev.APPROVAL_GRANTED))
    verification = [e for e in run["events"] if e["type"] == ev.VERIFICATION][-1]
    assert verification["data"]["passed"] is True
    assert run["pending_approval"] is None


def test_reject_sends_nothing_and_ends_visibly_incomplete(api, harness):
    run_id = start(api)
    pending = wait_status(api, run_id, "awaiting_approval")["pending_approval"]
    assert post(api, run_id, f"approvals/{pending['id']}/reject").status_code == 200
    run = wait_status(api, run_id, "rejected")

    assert invitations(harness) == []
    assert "NOT sent" in run["summary"]
    assert types(run)[-1] == ev.REJECTED and ev.APPROVAL_GRANTED not in types(run)
    assert run["details"]["invitation"] == "rejected"
    assert run["approvals"][0]["status"] == "rejected"
    # The safe work stays done and is reported.
    assert len([i for i in app_state(harness)["interviews"] if i["candidate_id"] == AARAV]) == 1


def test_stop_while_awaiting_approval(api, harness):
    run_id = start(api)
    pending = wait_status(api, run_id, "awaiting_approval")["pending_approval"]
    assert post(api, run_id, "stop").status_code == 200
    run = wait_status(api, run_id, "stopped")

    assert invitations(harness) == []
    assert types(run)[-1] == ev.STOPPED and "send_invitation: awaiting_approval" in run["summary"]
    assert api.get(f"/api/runs/{run_id}").json()["approvals"][0]["status"] == "cancelled"
    # A stale click on the cancelled approval must not revive anything.
    assert post(api, run_id, f"approvals/{pending['id']}/approve").status_code == 409
    time.sleep(0.5)
    assert invitations(harness) == []


def test_pause_holds_the_agent_until_continued(api_env, harness):
    api = api_env.client
    run_id = start(api)
    assert post(api, run_id, "pause").status_code == 200
    run = wait_status(api, run_id, "paused", "awaiting_approval")
    if run["status"] == "paused":  # the normal case: pause lands at a checkpoint before the gate
        n = len(run["events"])
        time.sleep(0.8)
        assert len(api.get(f"/api/runs/{run_id}").json()["events"]) == n  # nothing runs while paused
        assert ev.PAUSED in types(run)
        assert post(api, run_id, "continue").status_code == 200
    run = wait_status(api, run_id, "awaiting_approval")
    assert post(api, run_id, "continue").status_code == 409  # nothing to continue
    post(api, run_id, f"approvals/{run['pending_approval']['id']}/approve")
    assert wait_status(api, run_id, "completed")
    assert len(invitations(harness)) == 1


def test_stop_during_pause(api, harness):
    run_id = start(api)
    post(api, run_id, "pause")
    wait_status(api, run_id, "paused", "awaiting_approval")
    assert post(api, run_id, "stop").status_code == 200
    wait_status(api, run_id, "stopped")
    assert invitations(harness) == []


def test_control_endpoints_reject_invalid_requests(api, harness):
    assert post(api, "nope", "stop").status_code == 404
    run_id = start(api)
    run = wait_status(api, run_id, "awaiting_approval")
    assert post(api, run_id, "resume").status_code == 409  # not interrupted
    assert api.post("/api/runs", json={"goal": GOAL}).status_code == 409  # one run at a time
    assert post(api, run_id, "approvals/missing/approve").status_code == 404
    aid = run["pending_approval"]["id"]
    assert post(api, run_id, f"approvals/{aid}/approve").status_code == 200
    wait_status(api, run_id, "completed")
    assert post(api, run_id, f"approvals/{aid}/approve").status_code == 409  # decided once
    assert post(api, run_id, f"approvals/{aid}/reject").status_code == 409
    assert len(invitations(harness)) == 1


def test_rerunning_a_finished_goal_sends_nothing_again(api, harness):
    first = start(api)
    aid = wait_status(api, first, "awaiting_approval")["pending_approval"]["id"]
    post(api, first, f"approvals/{aid}/approve")
    wait_status(api, first, "completed")

    second = start(api, reset=False)
    run = wait_status(api, second, "completed", "awaiting_approval")
    assert run["status"] == "completed" and ev.APPROVAL_REQUESTED not in types(run)  # no new approval
    assert len(invitations(harness)) == 1
    assert "already sent" in run["summary"]


def test_opting_out_of_the_invitation(api, harness):
    run_id = start(api, GOAL + ", without sending an invitation")
    run = wait_status(api, run_id, "completed")
    assert ev.APPROVAL_REQUESTED not in types(run) and invitations(harness) == []


# ---- the gate cannot be bypassed ------------------------------------------------------------

PAYLOAD = {"candidate_id": AARAV, "interview_id": "int-1", "to": "aarav.sharma@example.com",
           "subject": "s", "message": "m"}


def test_driver_refuses_without_a_valid_grant(driver, harness):
    with pytest.raises(ApprovalRequired):
        driver.send_invitation(ApprovalGrant("x", "send_invitation", payload_hash(PAYLOAD), lambda: False),
                               PAYLOAD)  # approval no longer in force (rejected / cancelled)
    with pytest.raises(ApprovalRequired):  # approval is for different content
        driver.send_invitation(ApprovalGrant("x", "send_invitation", payload_hash({**PAYLOAD, "subject": "other"}),
                                             lambda: True), PAYLOAD)
    with pytest.raises(ApprovalRequired):  # approval is for a different action
        driver.send_invitation(ApprovalGrant("x", "delete_candidate", payload_hash(PAYLOAD), lambda: True),
                               PAYLOAD)
    assert invitations(harness) == []


def test_goal_with_invitation_but_no_approval_channel_does_nothing(driver, harness, log):
    from app.agent.runner import AgentOptions, run_goal

    result = run_goal(GOAL, driver, harness, log, options=AgentOptions(retry_backoff_seconds=0), control=None)
    assert result.status == "blocked" and "approval" in result.blocker
    assert invitations(harness) == [] and app_state(harness)["interviews"][-1]["candidate_id"] != AARAV


# ---- persistence and safe resume --------------------------------------------------------------

class _Crash(BaseException):
    """Stands in for the process dying: not an Exception, so nothing in the agent can catch it."""


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_run_survives_a_crash_and_resumes_without_repeating_work(api_env, harness, monkeypatch):
    client = api_env.client

    class DiesWhileWaiting(RunControl):
        def request_approval(self, *args, **kwargs):
            self._cond.wait = lambda *_: (_ for _ in ()).throw(_Crash())  # die once the request is saved
            return super().request_approval(*args, **kwargs)

    from app.agent import manager as manager_module
    monkeypatch.setattr(manager_module, "RunControl", DiesWhileWaiting)
    run_id = start(client)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:  # the thread is dead; the row still says it is waiting
        run = client.get(f"/api/runs/{run_id}").json()
        if run["pending_approval"] and not run["live"] or run["approvals"]:
            break
        time.sleep(0.1)
    time.sleep(0.5)
    assert invitations(harness) == []

    # "Restart": a new manager on the same database finds the orphaned run and marks it resumable.
    monkeypatch.undo()
    fresh = RunManager(api_env.tools, api_env.options, api_env.session_factory)
    from app import main
    monkeypatch.setattr(main, "runs", fresh)
    assert fresh.recover() == 1
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "interrupted"
    assert run["events"] and run["checkpoint"]["steps"]["update_status"] == "done"  # progress was saved
    approval = run["pending_approval"]
    assert approval and approval["status"] == "pending"

    # The user's decision is recorded even though no agent is running, then the run is resumed.
    assert post(client, run_id, f"approvals/{approval['id']}/approve").status_code == 200
    assert invitations(harness) == []  # approving alone sends nothing
    assert post(client, run_id, "resume").status_code == 202
    run = wait_status(client, run_id, "completed")

    t = types(run)
    assert ev.RUN_RESTARTED in t and t.count(ev.APPROVAL_REQUESTED) == 1  # the approval was reused
    assert len([i for i in app_state(harness)["interviews"] if i["candidate_id"] == AARAV]) == 1
    assert len(invitations(harness)) == 1  # sent exactly once


def test_stopping_an_interrupted_run(api_env, harness):
    from app.agent.manager import RunManager

    manager = RunManager(api_env.tools, api_env.options, api_env.session_factory)
    manager.store.create_run("orphan0001", GOAL, None)
    manager.store.set_status("orphan0001", "awaiting_approval")
    assert manager.recover() == 1
    manager.stop("orphan0001")
    run = manager.get("orphan0001")
    assert run["status"] == "stopped" and run["events"][-1]["type"] == ev.STOPPED


# ---- goal parsing ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    (GOAL, True),
    (GOAL + ". Do not send an invitation.", False),
    (GOAL + ", without sending the invite", False),
    (GOAL + " and don't email them", False),
    (GOAL + " and send them an invitation", True),
])
def test_invitation_is_default_and_opt_out(text, expected):
    g = parse_interview_goal(text, ["Aarav Sharma"], [], today=date(2026, 10, 1))
    assert g.send_invitation is expected
