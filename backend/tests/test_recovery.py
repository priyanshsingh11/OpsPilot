"""Phase 5: reliable failure recovery, end to end in a real browser.

Each test boots from seed data, arms a deterministic failure in the demo app,
runs the agent on a plain-English goal, and checks the app's records directly.
"""

from app.agent import events as ev
from app.agent.runner import AgentOptions, run_goal

from conftest import interviews_for

GOAL = "Schedule a Technical Screen for Aarav Sharma with Priya Nair on 2026-10-08 at 10:00"
AARAV = "cand-aarav"
FAST = AgentOptions(retry_backoff_seconds=0)


def run(driver, harness, log, scenario="none", goal=GOAL):
    return run_goal(goal, driver, harness, log, scenario=scenario, options=FAST)


def candidate_status(harness, cid):
    return next(c["status"] for c in harness.source_of_truth()["candidates"] if c["id"] == cid)


def subsequence(types, expected):
    it = iter(types)
    return all(t in it for t in expected)


def test_normal_execution(driver, harness, log):
    result = run(driver, harness, log)

    assert result.status == "completed"
    assert len(interviews_for(harness, AARAV, "Technical Screen")) == 1
    assert candidate_status(harness, AARAV) == "interview"
    assert ev.ACTION_FAILED not in log.types() and ev.RETRY not in log.types()
    assert log.types()[-1] == ev.COMPLETED


def test_failure_then_safe_retry(driver, harness, log):
    result = run(driver, harness, log, "transient")

    assert result.status == "completed" and result.details["attempts"] == 2
    rows = interviews_for(harness, AARAV, "Technical Screen")
    assert len(rows) == 1 and rows[0]["scheduled_at"] == "2026-10-08T10:00"
    assert subsequence(log.types(), [ev.ACTION, ev.ACTION_FAILED, ev.STATE_CHECK, ev.RECOVERY_STARTED,
                                     ev.RETRY, ev.ACTION_SUCCEEDED, ev.VERIFICATION, ev.COMPLETED])
    recovery = next(e for e in log.events if e["type"] == ev.RECOVERY_STARTED)
    assert recovery["data"]["decision"] == "retry"
    # The retry happened only after a post-failure state check found nothing.
    post = [e for e in log.events if e["type"] == ev.STATE_CHECK and e["data"]["phase"] == "post-failure"]
    assert post and post[0]["data"]["matching_interview_id"] is None


def test_failed_request_that_actually_saved_is_not_duplicated(driver, harness, log):
    result = run(driver, harness, log, "saved_but_failed")

    assert result.status == "completed"
    assert len(interviews_for(harness, AARAV, "Technical Screen")) == 1  # no duplicate
    assert ev.RETRY not in log.types()
    assert log.types().count(ev.ACTION) == 2  # one interview submit + one status update
    recovery = next(e for e in log.events if e["type"] == ev.RECOVERY_STARTED)
    assert recovery["data"]["decision"] == "no_retry"
    assert recovery["data"]["reason"] == "action_already_applied"
    assert candidate_status(harness, AARAV) == "interview"  # workflow continued


def test_blind_retry_would_have_duplicated(driver, harness):
    """Control: the app has no duplicate guard, so the agent's state check is what prevents one."""
    harness.arm_failure(1, "after_write")
    for _ in range(2):  # what `try: create() except: create()` does
        driver.submit_interview(AARAV, "Technical Screen", "2026-10-08T10:00", "Priya Nair")
    assert len(interviews_for(harness, AARAV, "Technical Screen")) == 2


def test_rerunning_a_completed_goal_creates_nothing(driver, harness, log):
    assert run(driver, harness, log).status == "completed"
    log2 = ev.RunLog("rerun")
    result = run_goal(GOAL, driver, harness, log2, options=FAST)

    assert result.status == "completed" and result.details["created_by_agent"] is False
    assert len(interviews_for(harness, AARAV, "Technical Screen")) == 1
    assert ev.ACTION not in log2.types() and log2.types().count(ev.SKIPPED) == 2


def test_unrecoverable_failure_reports_blocker(driver, harness, log):
    result = run(driver, harness, log, "outage")

    assert result.status == "blocked"
    assert "after 3 attempts" in result.blocker and "safe to re-run" in result.blocker
    assert interviews_for(harness, AARAV, "Technical Screen") == []  # nothing half-done
    assert log.types().count(ev.ACTION_FAILED) == 3
    assert log.types().count(ev.RETRY) == 2
    assert candidate_status(harness, AARAV) == "shortlisted"  # did not continue past the blocker
    assert log.types()[-1] == ev.BLOCKED


def test_recovers_once_outage_clears(driver, harness, log):
    """After a blocked run, re-running the same goal (service back) completes with one interview."""
    assert run(driver, harness, log, "outage").status == "blocked"
    result = run_goal(GOAL, driver, harness, ev.RunLog("again"), scenario="none", options=FAST)
    assert result.status == "completed"
    assert len(interviews_for(harness, AARAV, "Technical Screen")) == 1


def test_variation_without_code_change(driver, harness, log):
    goal = "Book a system design round for Ishaan Mehta with Rahul Verma on October 9, 2026 at 3pm"
    result = run(driver, harness, log, "saved_but_failed", goal)

    assert result.status == "completed"
    rows = interviews_for(harness, "cand-ishaan")
    assert [(r["round"], r["scheduled_at"], r["interviewer"]) for r in rows] == [
        ("System Design Round", "2026-10-09T15:00", "Rahul Verma")]


def test_conflicting_booking_needs_approval(driver, harness, log):
    # Seed: Sneha already has a Technical Screen on 2026-10-06 14:00 with Priya Nair.
    goal = "Schedule a Technical Screen for Sneha Reddy with Priya Nair on 2026-10-09 at 11:00"
    result = run(driver, harness, log, "none", goal)

    assert result.status == "blocked" and "needs your approval" in result.blocker
    assert ev.ACTION not in log.types()
    assert len(interviews_for(harness, "cand-sneha")) == 1


def test_rejected_candidate_needs_approval(driver, harness, log):
    goal = "Schedule a Technical Screen for Kabir Singh with Priya Nair on 2026-10-09 at 11:00"
    result = run(driver, harness, log, "none", goal)
    assert result.status == "blocked" and "rejected" in result.blocker
    assert interviews_for(harness, "cand-kabir") == []


def test_unparseable_goal_takes_no_action(driver, harness, log):
    result = run(driver, harness, log, "none", "Schedule something for Nobody sometime")
    assert result.status == "blocked"
    assert ev.ACTION not in log.types()
    assert len(harness.source_of_truth()["interviews"]) == 1  # seed only
