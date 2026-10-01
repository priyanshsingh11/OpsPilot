"""Runs one goal end to end: parse -> plan -> act with recovery -> verify -> report."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import events as ev
from .driver import AppUnavailable, RecruitAppDriver
from .goal import GoalError, parse_interview_goal
from .scenarios import SCENARIOS
from .scheduling import InterviewRequest, ensure_status, schedule_interview

TARGET_STATUS = "Interview Scheduled"


@dataclass
class AgentOptions:
    max_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    step_delay_seconds: float = 0.0


@dataclass
class RunResult:
    status: str  # completed | blocked
    summary: str
    blocker: str | None = None
    details: dict = field(default_factory=dict)


def _blocked(log: ev.RunLog, blocker: str, **details) -> RunResult:
    log.emit(ev.BLOCKED, blocker, **details)
    return RunResult("blocked", "Goal not completed", blocker=blocker, details=details)


def run_goal(goal: str, driver: RecruitAppDriver, log: ev.RunLog, *, scenario: str | None = None,
             reset_demo_data: bool = False, options: AgentOptions | None = None) -> RunResult:
    opts = options or AgentOptions()
    log.pace_seconds = opts.step_delay_seconds
    log.emit(ev.GOAL_RECEIVED, goal, scenario=scenario)
    try:
        return _run(goal, driver, log, scenario, reset_demo_data, opts)
    except AppUnavailable as exc:
        return _blocked(log, f"Recruitment app unavailable: {exc}")
    except Exception as exc:  # never leave a run without a terminal event
        return _blocked(log, f"Unexpected agent error: {exc.__class__.__name__}: {exc}")


def _run(goal, driver, log, scenario, reset_demo_data, opts) -> RunResult:
    # Demo harness: reproducible environment and failure injection (not agent actions).
    if reset_demo_data:
        driver.reset_demo_data()
        log.emit(ev.SETUP, "Demo harness reset the recruitment app to its seed data.")
    if scenario is not None:
        sc = SCENARIOS[scenario]
        driver.arm_failure(sc.failing_attempts, sc.mode)
        log.emit(ev.SETUP, f"Demo harness armed scenario '{sc.label}': {sc.description}",
                 scenario=sc.key, failing_attempts=sc.failing_attempts, mode=sc.mode)

    # Understand the goal using what the app actually contains.
    candidates = driver.list_candidates()
    if not candidates:
        return _blocked(log, "The recruitment app lists no candidates.")
    interviewers = driver.read_candidate(candidates[0].id).interviewer_options
    try:
        g = parse_interview_goal(goal, [c.name for c in candidates], interviewers)
    except GoalError as exc:
        return _blocked(log, "Cannot act on this goal: " + " ".join(exc.problems), problems=exc.problems)
    cand = next(c for c in candidates if c.name == g.candidate_name)
    req = InterviewRequest(cand.id, cand.name, g.interviewer, g.date, g.start_time, g.duration_minutes)
    log.emit(ev.PLAN, f"Plan: (1) schedule a {req.describe()}; (2) set {cand.name}'s status to "
             f"'{TARGET_STATUS}'; (3) verify both in the app's records.",
             request=req.__dict__, assumptions=g.assumptions)

    if cand.status == "Rejected":
        return _blocked(log, f"{cand.name} is marked Rejected. Scheduling an interview would reverse a "
                        "hiring decision, which exceeds this goal's authority; needs your approval.",
                        candidate_id=cand.id)

    # Step 1: the non-idempotent action, with state-aware recovery.
    step1 = schedule_interview(driver, log, req, max_attempts=opts.max_attempts,
                               backoff_seconds=opts.retry_backoff_seconds)
    if not step1.ok:
        return _blocked(log, step1.blocker or step1.summary, step="schedule_interview",
                        attempts=step1.attempts)

    # Step 2: continue the workflow once the interview is confirmed.
    step2 = ensure_status(driver, log, cand.id, TARGET_STATUS)
    if not step2.ok:
        return _blocked(log, f"Interview #{step1.interview_id} is scheduled, but the status update "
                        f"did not complete: {step2.blocker}", step="update_status",
                        interview_id=step1.interview_id)

    summary = f"{step1.summary} {cand.name}'s status is '{TARGET_STATUS}'."
    details = {"interview_id": step1.interview_id, "candidate_id": cand.id, "attempts": step1.attempts,
               "created_by_agent": step1.created_by_agent, "request": req.__dict__}
    log.emit(ev.COMPLETED, summary, **details)
    return RunResult("completed", summary, details=details)
