"""Runs one goal end to end: parse -> plan -> act with recovery -> verify -> report."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import events as ev
from .control import RunControl, RunStopped
from .driver import AppUnavailable, BrowserDriver, HarnessClient
from .goal import GoalError, parse_interview_goal
from .invitation import send_invitation_with_approval
from .scenarios import SCENARIOS
from .scheduling import InterviewRequest, ensure_status, schedule_interview

logger = logging.getLogger("opspilot.agent")

TARGET_STATUS = "interview"


@dataclass
class AgentOptions:
    max_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    step_delay_seconds: float = 0.0


@dataclass
class RunResult:
    status: str  # completed | blocked | stopped | rejected
    summary: str
    blocker: str | None = None
    details: dict = field(default_factory=dict)


def _blocked(log: ev.RunLog, blocker: str, **details) -> RunResult:
    log.emit(ev.BLOCKED, blocker, **details)
    return RunResult("blocked", "Goal not completed", blocker=blocker, details=details)


def run_goal(goal: str, driver: BrowserDriver, harness: HarnessClient, log: ev.RunLog, *,
             scenario: str | None = None, reset_demo_data: bool = False,
             options: AgentOptions | None = None, control: RunControl | None = None) -> RunResult:
    """Run one goal. `control` provides pause/stop and the approval gate; without it the agent
    has no way to ask for approval, so it will not send invitations."""
    opts = options or AgentOptions()
    log.pace_seconds = opts.step_delay_seconds
    if log.events:  # the log already has history: this run was interrupted and is being resumed
        steps = (control.checkpoint_state.get("steps") if control else None) or {}
        log.emit(ev.RUN_RESTARTED, "Resuming an interrupted run. Every step re-checks the app's current "
                 f"state before acting, so finished work is not repeated. Saved progress: {steps or 'none'}.")
    else:
        log.emit(ev.GOAL_RECEIVED, goal, scenario=scenario)
    try:
        return _run(goal, driver, harness, log, scenario, reset_demo_data, opts, control)
    except RunStopped:
        return _stopped(log, control)
    except AppUnavailable as exc:
        return _blocked(log, f"Recruitment app unavailable: {exc}")
    except Exception as exc:  # never leave a run without a terminal event
        logger.exception("unexpected agent error")
        return _blocked(log, f"Unexpected agent error: {exc.__class__.__name__}: {exc}")


def _stopped(log: ev.RunLog, control: RunControl | None) -> RunResult:
    steps = (control.checkpoint_state.get("steps") if control else None) or {}
    done = ", ".join(f"{k}: {v}" for k, v in steps.items()) or "nothing yet"
    msg = f"Stopped by the user. Progress at the time: {done}. No further actions were taken."
    log.emit(ev.STOPPED, msg, steps=steps)
    return RunResult("stopped", msg, details={"steps": steps})


def _run(goal, driver, harness, log, scenario, reset_demo_data, opts, control) -> RunResult:
    checkpoint = control.checkpoint if control else (lambda: None)
    save = control.save if control else (lambda **_: None)
    # Demo harness: reproducible environment and failure injection (not agent actions).
    if reset_demo_data:
        harness.reset_demo_data()
        log.emit(ev.SETUP, "Demo harness reset the recruitment app to its seed data.")
    if scenario is not None:
        sc = SCENARIOS[scenario]
        harness.arm_failure(sc.failing_attempts, sc.mode)
        log.emit(ev.SETUP, f"Demo harness armed scenario '{sc.label}': {sc.description}",
                 scenario=sc.key, failing_attempts=sc.failing_attempts, mode=sc.mode)

    # Understand the goal using what the app actually contains (read through the browser).
    candidates = driver.list_candidates()
    try:
        g = parse_interview_goal(goal, [c.name for c in candidates], driver.hiring_managers())
    except GoalError as exc:
        return _blocked(log, "Cannot act on this goal: " + " ".join(exc.problems), problems=exc.problems)
    cand = next(c for c in candidates if c.name == g.candidate_name)
    req = InterviewRequest(cand.id, cand.name, g.round_name, g.scheduled_at, g.interviewer)
    plan = (f"Plan: (1) schedule {req.describe()}; (2) set {cand.name}'s status to '{TARGET_STATUS}'; "
            + ("(3) show you the invitation email and wait for your approval; (4) send it once approved; "
               "(5) verify everything in the app's records." if g.send_invitation
               else "(3) verify both in the app's records. No invitation will be sent."))
    log.emit(ev.PLAN, plan, request=req.__dict__, assumptions=g.assumptions)
    save(request=req.__dict__, send_invitation=g.send_invitation)
    if g.send_invitation and control is None:
        return _blocked(log, "This goal includes sending an invitation, which needs your approval, but this "
                        "run has no approval channel. Nothing was done.")

    if cand.status == "rejected":
        return _blocked(log, f"{cand.name} is marked rejected. Scheduling an interview would reverse a "
                        "hiring decision, which exceeds this goal's authority; needs your approval.",
                        candidate_id=cand.id)

    # Step 1: the non-idempotent action, with state-aware recovery.
    checkpoint()
    step1 = schedule_interview(driver, harness, log, req, max_attempts=opts.max_attempts,
                               backoff_seconds=opts.retry_backoff_seconds, checkpoint=checkpoint)
    if not step1.ok:
        return _blocked(log, step1.blocker or step1.summary, step="schedule_interview",
                        attempts=step1.attempts)
    save(steps={"schedule_interview": "done"}, interview_id=step1.interview_id)

    # Step 2: continue the workflow once the interview is confirmed.
    checkpoint()
    step2 = ensure_status(driver, harness, log, cand.id, TARGET_STATUS)
    if not step2.ok:
        return _blocked(log, f"Interview {step1.interview_id} is scheduled, but the status update "
                        f"did not complete: {step2.blocker}", step="update_status",
                        interview_id=step1.interview_id)
    save(steps={"update_status": "done"})

    summary = f"{step1.summary} {cand.name}'s status is '{TARGET_STATUS}'."
    details = {"interview_id": step1.interview_id, "candidate_id": cand.id, "attempts": step1.attempts,
               "created_by_agent": step1.created_by_agent, "request": req.__dict__}

    # Step 3: the high-impact action. Gated on the user's approval.
    if g.send_invitation:
        inv = send_invitation_with_approval(driver, harness, log, control, req, step1.interview_id,
                                            max_attempts=opts.max_attempts,
                                            backoff_seconds=opts.retry_backoff_seconds)
        details["invitation"] = inv.status
        if inv.status == "rejected":
            msg = (f"{step1.summary} {cand.name}'s status is '{TARGET_STATUS}'. {inv.summary} "
                   "The invitation was NOT sent.")
            log.emit(ev.REJECTED, msg, **details)
            return RunResult("rejected", msg, details=details)
        if not inv.ok:
            return _blocked(log, f"The interview is scheduled and the status is updated, but the invitation "
                            f"was not completed: {inv.blocker or inv.summary}", step="send_invitation",
                            interview_id=step1.interview_id)
        details["invitation_id"] = inv.invitation_id
        summary += (" Invitation already sent earlier; none sent again." if inv.status == "already_sent"
                    else " Invitation sent after your approval and verified.")
    log.emit(ev.COMPLETED, summary, **details)
    return RunResult("completed", summary, details=details)
