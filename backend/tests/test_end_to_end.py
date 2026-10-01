"""Stage 9: end-to-end tests of the five demo scenarios, through the real API.

FastAPI app + background agent thread + Chromium + the demo recruitment app; outcomes are
checked in the demo app's own records, not in what the agent reports.
"""

import time
from datetime import date, timedelta
from pathlib import Path

from app.agent import events as ev
from test_human_control import post, start, types, wait_status

TC1 = "Schedule interviews for all shortlisted AI Engineer candidates tomorrow afternoon."
TC2 = "Schedule 45-minute interviews for shortlisted Full Stack Engineer candidates on Friday."
AI_SHORTLIST = {"cand-aarav", "cand-diya"}
FULL_STACK_SHORTLIST = {"cand-kavya", "cand-rehan"}
FINISHED = ("completed", "partially_completed", "failed", "blocked", "stopped", "rejected")


def approve_all(client, run_id, timeout=60):
    """Approve each invitation as it is requested, until the run ends. Returns (run, approvals seen)."""
    deadline, seen = time.monotonic() + timeout, []
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in FINISHED:
            return run, seen
        pending = run.get("pending_approval")
        if run["status"] == "awaiting_approval" and pending:
            seen.append(pending)
            assert post(client, run_id, f"approvals/{pending['id']}/approve").status_code == 200
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def state(harness):
    return harness.source_of_truth()


def mine(items, ids):
    return [i for i in items if i["candidate_id"] in ids]


def final_checks(run):
    return [e for e in run["events"] if e["type"] == ev.VERIFICATION and e["data"].get("final")]


def test_case_1_normal_execution(api, harness, tmp_path):
    before = state(harness)
    run_id = start(api, TC1)

    # The approval appears and holds the run: nothing is sent while waiting.
    run = wait_status(api, run_id, "awaiting_approval", timeout=60)
    assert state(harness)["invitations"] == []
    plan = next(e for e in run["events"] if e["type"] == ev.PLAN)
    assert plan["data"]["batch"] is True
    assert {t["id"] for t in plan["data"]["targets"]} == AI_SHORTLIST  # candidates found through the UI

    run, approvals = approve_all(api, run_id)
    assert run["status"] == "completed", run["blocker"]
    assert len(approvals) == 2  # one approval per email, each resumed the run
    email = approvals[0]["details"]["email"]  # what the user approves is a clean, correct email
    assert email["subject"] == "Interview invitation: Interview for AI Engineer"
    assert "invite you to an Interview for the AI Engineer role" in email["message"]

    after = state(harness)
    tomorrow = date.today() + timedelta(days=1)  # "tomorrow" on a weekend moves to the next business day
    while tomorrow.weekday() >= 5:
        tomorrow += timedelta(days=1)
    tomorrow = tomorrow.isoformat()
    created = mine(after["interviews"], AI_SHORTLIST)
    assert len(created) == 2
    for iv in created:  # tomorrow afternoon, 60 min, with the hiring manager
        assert iv["scheduled_at"].startswith(tomorrow) and "13:00" <= iv["scheduled_at"][11:16] <= "16:00"
        assert iv["interviewer"] == "Priya Nair" and iv["duration_minutes"] == 60
    assert {c["id"]: c["status"] for c in after["candidates"] if c["id"] in AI_SHORTLIST} == {
        "cand-aarav": "interview", "cand-diya": "interview"}
    assert len(mine(after["invitations"], AI_SHORTLIST)) == 2
    # Nothing else touched.
    others = {c["id"]: c["status"] for c in before["candidates"] if c["id"] not in AI_SHORTLIST}
    assert {c["id"]: c["status"] for c in after["candidates"] if c["id"] in others} == others

    checks = final_checks(run)
    assert checks and all(e["data"]["passed"] for e in checks)
    assert {e["data"]["check"] for e in checks} >= {"interviews_exist", "statuses", "no_duplicates",
                                                    "no_unintended_changes", "no_double_booking"}
    evidence = [e for e in run["events"] if e["type"] == ev.EVIDENCE]
    assert evidence and Path(evidence[0]["data"]["screenshot"]).exists()
    assert run["details"]["actions"], "action ledger is recorded"


def test_case_2_variation_without_code_change(api, harness):
    run_id = start(api, TC2)
    run, _ = approve_all(api, run_id)
    assert run["status"] == "completed", run["blocker"]

    friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)
    created = mine(state(harness)["interviews"], FULL_STACK_SHORTLIST | {"cand-zoya"})
    assert {i["candidate_id"] for i in created} == FULL_STACK_SHORTLIST  # Zoya is only 'applied'
    for iv in created:
        assert iv["duration_minutes"] == 45
        assert iv["scheduled_at"].startswith(friday.isoformat())
        assert iv["interviewer"] == "Neha Kulkarni"  # the Full Stack hiring manager
    # 45-minute slots are packed back to back without overlap.
    starts = sorted(i["scheduled_at"][11:16] for i in created)
    assert starts == ["10:00", "10:45"]


def test_case_3_failure_detected_and_recovered_without_duplicate(api, harness):
    run_id = start(api, TC1, scenario="saved_but_failed")
    run, _ = approve_all(api, run_id)
    assert run["status"] == "completed", run["blocker"]
    t = types(run)
    assert ev.ACTION_FAILED in t  # failure appears
    post_checks = [e for e in run["events"] if e["type"] == ev.STATE_CHECK and e["data"].get("phase") == "post-failure"]
    assert post_checks and post_checks[0]["data"]["matching_interview_id"]  # state checked: it was saved
    decision = next(e for e in run["events"] if e["type"] == ev.RECOVERY_STARTED)
    assert decision["data"]["decision"] == "no_retry"  # duplicate action avoided
    assert ev.RETRY not in t
    created = mine(state(harness)["interviews"], AI_SHORTLIST)
    assert len(created) == 2  # one each, no duplicate
    assert all(e["data"]["passed"] for e in final_checks(run))


def test_case_3b_transient_failure_retried_safely(api, harness):
    run_id = start(api, TC1, scenario="transient")
    run, _ = approve_all(api, run_id)
    assert run["status"] == "completed", run["blocker"]
    decision = next(e for e in run["events"] if e["type"] == ev.RECOVERY_STARTED)
    assert decision["data"]["decision"] == "retry" and ev.RETRY in types(run)
    assert len(mine(state(harness)["interviews"], AI_SHORTLIST)) == 2


def test_case_4_pause_and_resume(api, harness):
    run_id = start(api, TC1)
    assert post(api, run_id, "pause").status_code == 200
    run = wait_status(api, run_id, "paused", timeout=60)
    n_events, n_interviews = len(run["events"]), len(state(harness)["interviews"])
    time.sleep(1.0)
    assert len(api.get(f"/api/runs/{run_id}").json()["events"]) == n_events  # stopped safely
    assert len(state(harness)["interviews"]) == n_interviews  # no action while paused

    assert post(api, run_id, "continue").status_code == 200  # the user can resume
    run, _ = approve_all(api, run_id)
    assert run["status"] == "completed", run["blocker"]
    assert ev.PAUSED in types(run) and ev.RESUMED in types(run)
    created = mine(state(harness)["interviews"], AI_SHORTLIST)
    assert len(created) == 2  # no duplicate after resuming
    assert len(mine(state(harness)["invitations"], AI_SHORTLIST)) == 2


def test_case_5a_partial_failure_is_reported_as_partial(api, harness):
    run_id = start(api, TC1, scenario="partial_outage")
    run, _ = approve_all(api, run_id)
    assert run["status"] == "partially_completed"  # never "completed"
    t = types(run)
    assert ev.COMPLETED not in t and ev.PARTIALLY_COMPLETED in t and ev.TARGET_BLOCKED in t
    remaining = run["details"]["remaining"]
    assert len(remaining) == 1 and "Aarav Sharma" in remaining[0] and "safe to re-run" in remaining[0]
    after = state(harness)
    assert mine(after["interviews"], {"cand-aarav"}) == []  # the blocked one: nothing half-done
    assert len(mine(after["interviews"], {"cand-diya"})) == 1  # the other one went through
    assert next(c for c in after["candidates"] if c["id"] == "cand-aarav")["status"] == "shortlisted"


def test_case_5b_unrecoverable_failure_is_reported_as_failed(api, harness):
    run_id = start(api, TC1, scenario="outage")
    run, approvals = approve_all(api, run_id)
    assert run["status"] == "failed"
    assert approvals == []  # nothing to approve: no interview exists
    assert ev.COMPLETED not in types(run)
    assert len(run["details"]["remaining"]) == 2
    assert mine(state(harness)["interviews"], AI_SHORTLIST) == []
    failed = {e["data"]["check"]: e["data"]["passed"] for e in final_checks(run)}
    assert failed["interviews_exist"] is False and failed["no_unintended_changes"] is True


def test_rerun_after_completion_does_nothing(api, harness):
    run, _ = approve_all(api, start(api, TC1))
    assert run["status"] == "completed"
    n = len(state(harness)["interviews"])
    rerun, approvals = approve_all(api, start(api, TC1, reset=False))
    assert rerun["status"] == "completed" and approvals == []
    assert "Nothing to do" in rerun["summary"]
    assert len(state(harness)["interviews"]) == n


def test_rejecting_one_invitation_is_partial_not_completed(api, harness):
    run_id = start(api, TC1)
    first = wait_status(api, run_id, "awaiting_approval", timeout=60)["pending_approval"]
    assert post(api, run_id, f"approvals/{first['id']}/reject").status_code == 200
    run, _ = approve_all(api, run_id)  # approve the second one
    assert run["status"] == "partially_completed"
    assert any("You rejected" in r for r in run["details"]["remaining"])
    assert len(mine(state(harness)["invitations"], AI_SHORTLIST)) == 1  # only the approved email was sent


def test_rejecting_every_invitation_is_partial_not_failed(api, harness):
    """The interviews and statuses really changed; only the emails were withheld. That is not FAILED."""
    run_id = start(api, TC1)
    while True:
        run = wait_status(api, run_id, "awaiting_approval", *FINISHED, timeout=60)
        if run["status"] in FINISHED:
            break
        assert post(api, run_id, f"approvals/{run['pending_approval']['id']}/reject").status_code == 200

    assert run["status"] == "partially_completed", run["summary"]
    s = state(harness)
    assert s["invitations"] == []  # nothing was sent
    assert {i["candidate_id"] for i in s["interviews"]} >= AI_SHORTLIST  # the safe work is real
    assert "rejected" in run["blocker"]
