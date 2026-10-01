"""Runs one goal end to end: parse -> plan -> act with recovery -> verify -> report."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import events as ev
from .control import RunControl, RunStopped
from .driver import AppUnavailable, BrowserDriver, HarnessClient
from .. import verification
from .evidence import ActionLedger
from .goal import BatchInterviewGoal, GoalError, parse_batch_goal, parse_interview_goal
from .invitation import send_invitation_with_approval
from .planning import Booking, free_slots
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
    status: str  # completed | partially_completed | failed | blocked | stopped | rejected
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
    jobs = driver.list_jobs()
    try:
        batch = parse_batch_goal(goal, [j.title for j in jobs])
    except GoalError as exc:
        return _blocked(log, "Cannot act on this goal: " + " ".join(exc.problems), problems=exc.problems)
    if batch is not None:
        return _run_batch(batch, jobs, driver, harness, log, opts, control, checkpoint, save)

    candidates = driver.list_candidates()
    try:
        g = parse_interview_goal(goal, [c.name for c in candidates], driver.hiring_managers())
    except GoalError as exc:
        return _blocked(log, "Cannot act on this goal: " + " ".join(exc.problems), problems=exc.problems)
    cand = next(c for c in candidates if c.name == g.candidate_name)
    req = InterviewRequest(cand.id, cand.name, g.round_name, g.scheduled_at, g.interviewer, g.duration_minutes)
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


def _run_batch(g: BatchInterviewGoal, jobs, driver, harness, log, opts, control, checkpoint, save) -> RunResult:
    """Schedule one interview per matching candidate, then verify the whole batch independently."""
    ledger = ActionLedger()
    job = next(j for j in jobs if j.title == g.job_title)
    interviewer = g.interviewer or job.hiring_manager

    find = ledger.begin("find_candidates", f"Find {g.candidate_status} {job.title} candidates",
                        {"job": job.title, "status": g.candidate_status})
    targets = driver.filter_candidates(job.id, g.candidate_status)
    names = ", ".join(c.name for c in targets) or "none"
    ledger.finish(find, "succeeded", result={"count": len(targets), "candidates": names},
                  title=f"Found {len(targets)} {g.candidate_status} {job.title} candidate(s)")
    log.emit(ev.STATE_CHECK, f"Filtered the {job.title} candidate list to '{g.candidate_status}' in the "
             f"browser: found {len(targets)} ({names}).", phase="discovery",
             candidates=[c.__dict__ for c in targets])

    # Baseline for verification, read before the agent changes anything.
    before = harness.source_of_truth()

    slots_rec = ledger.begin("find_slots", f"Find free {g.duration_minutes}-min slots for {interviewer}",
                             {"date": g.date, "window": f"{g.window_start}-{g.window_end}",
                              "duration_minutes": g.duration_minutes})
    rows, calendar_shot = driver.list_interviews()
    target_ids = {c.id for c in targets}
    existing = {r.candidate_id: r for r in rows if r.candidate_id in target_ids and r.status == "scheduled"
                and r.round.strip().lower() == g.round_name.lower()}
    need = [c for c in targets if c.id not in existing]
    slots = free_slots(len(need), g.date, g.window_start, g.window_end, g.duration_minutes, interviewer,
                       [Booking(r.scheduled_at, r.duration_minutes, r.interviewer, r.status) for r in rows])
    busy = [f"{r.scheduled_at[11:16]} ({r.duration_minutes} min)" for r in rows
            if r.status == "scheduled" and r.interviewer == interviewer and r.scheduled_at.startswith(g.date)]
    ledger.finish(slots_rec, "succeeded" if len(slots) == len(need) else "failed", evidence=calendar_shot,
                  result={"slots": slots, "interviewer_busy": busy},
                  error=None if len(slots) == len(need) else f"only {len(slots)} free slot(s) for {len(need)}",
                  title=f"Found {len(slots)} free slot(s) for {interviewer} on {g.date}")
    log.emit(ev.STATE_CHECK, f"Read the interview calendar: {interviewer} is busy on {g.date} at "
             f"{', '.join(busy) or 'no times'}; found {len(slots)} free {g.duration_minutes}-min slot(s) between "
             f"{g.window_start} and {g.window_end}"
             + (f"; {len(existing)} candidate(s) already have a '{g.round_name}'." if existing else "."),
             phase="planning", slots=slots, screenshot=calendar_shot)

    requests: dict[str, InterviewRequest | None] = {}
    free = iter(slots)
    for c in targets:
        if c.id in existing:
            r = existing[c.id]
            requests[c.id] = InterviewRequest(c.id, c.name, r.round, r.scheduled_at, r.interviewer, r.duration_minutes)
        else:
            slot = next(free, None)
            requests[c.id] = (InterviewRequest(c.id, c.name, g.round_name, slot, interviewer, g.duration_minutes)
                              if slot else None)
    lines = [f"{c.name} at {requests[c.id].scheduled_at[11:16]}" if requests[c.id] else f"{c.name}: no free slot"
             for c in targets]
    log.emit(ev.PLAN, f"Plan: schedule a {g.duration_minutes}-min '{g.round_name}' with {interviewer} on {g.date} "
             f"for {len(targets)} candidate(s) ({'; '.join(lines) or 'none'}); set each status to "
             f"'{TARGET_STATUS}'; " + ("ask your approval before each invitation email; " if g.send_invitation
                                        else "no invitations; ") + "then verify everything in the app's records.",
             batch=True, targets=[{"id": c.id, "name": c.name, "slot": requests[c.id].scheduled_at
                                   if requests[c.id] else None} for c in targets],
             assumptions=g.assumptions)
    save(plan={c.name: (requests[c.id].scheduled_at if requests[c.id] else None) for c in targets})

    reasons: dict[str, str] = {}
    interview_ids: dict[str, str] = {}

    def block(c, reason: str) -> None:
        reasons[c.id] = reason
        log.emit(ev.TARGET_BLOCKED, f"{c.name}: {reason} Continuing with the remaining candidates.",
                 candidate_id=c.id)
        save(steps={c.name: "blocked"})

    for c in targets:
        req = requests[c.id]
        if req is None:
            block(c, f"No free {g.duration_minutes}-min slot for {interviewer} between {g.window_start} and "
                     f"{g.window_end} on {g.date}. Nothing was created for this candidate.")
            continue
        checkpoint()
        step1 = schedule_interview(driver, harness, log, req, ledger=ledger, max_attempts=opts.max_attempts,
                                   backoff_seconds=opts.retry_backoff_seconds, checkpoint=checkpoint)
        if not step1.ok:
            block(c, step1.blocker or step1.summary)
            continue
        interview_ids[c.id] = step1.interview_id
        checkpoint()
        step2 = ensure_status(driver, harness, log, c.id, TARGET_STATUS, ledger=ledger)
        if not step2.ok:
            block(c, f"Interview {step1.interview_id} is scheduled, but the status update failed: {step2.blocker}")
            continue
        save(steps={c.name: "scheduled"})
        log.emit(ev.TARGET_DONE, f"{c.name}: interview {step1.interview_id} scheduled for "
                 f"{req.scheduled_at.replace('T', ' ')} and status set to '{TARGET_STATUS}'.",
                 candidate_id=c.id, interview_id=step1.interview_id)

    if g.send_invitation:
        for c in targets:
            if c.id in reasons or c.id not in interview_ids:
                continue
            if control is None:
                reasons[c.id] = "This run has no approval channel, so no invitation could be sent."
                continue
            inv = send_invitation_with_approval(driver, harness, log, control, requests[c.id], interview_ids[c.id],
                                                max_attempts=opts.max_attempts,
                                                backoff_seconds=opts.retry_backoff_seconds)
            if inv.status == "rejected":
                reasons[c.id] = "You rejected sending the invitation, so it was not sent."
            elif not inv.ok:
                reasons[c.id] = inv.blocker or inv.summary
            save(steps={c.name: "invited" if inv.ok else f"invitation {inv.status}"})

    # Independent verification against the app's records (app/verification.py).
    after = harness.source_of_truth()
    expectations = [verification.Expectation(
        c.id, c.name, TARGET_STATUS, (requests[c.id].round_name if requests[c.id] else g.round_name),
        requests[c.id].scheduled_at if requests[c.id] else None,
        requests[c.id].interviewer if requests[c.id] else interviewer,
        requests[c.id].duration_minutes if requests[c.id] else g.duration_minutes,
        g.send_invitation, reasons.get(c.id)) for c in targets]
    report = verification.verify(expectations, before, after)
    for chk in report.checks:
        log.emit(ev.VERIFICATION, f"{chk.name}: {chk.detail}.", passed=chk.passed, check=chk.key, final=True)
    final_shot = driver.capture_page("/interviews", "final-calendar")
    if final_shot:
        log.emit(ev.EVIDENCE, "Captured the interview calendar after the run as evidence of the final state.",
                 screenshot=final_shot)

    details = {"batch": True, "outcome": report.outcome, "report": report.to_dict(),
               "remaining": report.remaining, "actions": ledger.to_list(),
               "interview_ids": interview_ids}
    terminal = {verification.COMPLETED: ev.COMPLETED, verification.PARTIAL: ev.PARTIALLY_COMPLETED,
                verification.FAILED: ev.FAILED}[report.outcome]
    message = report.headline + (" Remaining: " + " | ".join(report.remaining) if report.remaining else "")
    log.emit(terminal, message, outcome=report.outcome, remaining=report.remaining)
    return RunResult(report.outcome, report.headline,
                     blocker="; ".join(report.remaining) or None, details=details)
