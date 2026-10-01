"""State-aware interview creation with safe recovery.

Creating an interview is not idempotent: a failed request may or may not have
written the interview (the calendar can save the row and still time out). So a
failure never triggers a blind retry. Instead:

    attempt -> failure -> inspect current state -> did the action actually happen?
        yes                    -> do not retry; verify
        no, failure transient  -> retry (bounded), then verify
        no, failure permanent  -> stop with a concrete blocker
        state unreadable       -> stop; retrying blind could create a duplicate

Before the first attempt the same state check makes re-running a goal safe:
if the interview already exists, nothing is created.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import events as ev
from .driver import AppUnavailable, CandidatePage, InterviewRow, RecruitAppDriver, SubmitResult


@dataclass
class InterviewRequest:
    candidate_id: int
    candidate_name: str
    interviewer: str
    date: str
    start_time: str
    duration_minutes: int

    def describe(self) -> str:
        return (f"{self.duration_minutes}-min interview for {self.candidate_name} with "
                f"{self.interviewer} on {self.date} at {self.start_time}")


@dataclass
class StepOutcome:
    ok: bool
    summary: str
    interview_id: int | None = None
    attempts: int = 0
    created_by_agent: bool = False  # False when it already existed (no write by us)
    blocker: str | None = None


def _matches(row: InterviewRow | dict, req: InterviewRequest) -> bool:
    get = row.get if isinstance(row, dict) else lambda k: getattr(row, k)
    return (get("status") == "scheduled" and get("interviewer") == req.interviewer
            and get("date") == req.date and get("start_time") == req.start_time
            and int(get("duration_minutes")) == req.duration_minutes)


def _check_state(driver: RecruitAppDriver, log: ev.RunLog, req: InterviewRequest, phase: str):
    """Read the candidate page and classify what is there. Returns (page, match, other)."""
    page: CandidatePage = driver.read_candidate(req.candidate_id)
    scheduled = [i for i in page.interviews if i.status == "scheduled"]
    match = next((i for i in scheduled if _matches(i, req)), None)
    other = next((i for i in scheduled if i is not match), None)
    if match:
        finding = f"Interview #{match.id} matching the request already exists"
    elif other:
        finding = (f"No matching interview; {page.name} already has a different scheduled interview "
                   f"(#{other.id}: {other.date} {other.start_time} with {other.interviewer})")
    else:
        finding = f"No scheduled interview for {page.name}"
    log.emit(ev.STATE_CHECK, f"[{phase}] Opened {page.name}'s candidate page: {finding}.",
             phase=phase, candidate_status=page.status,
             scheduled_interviews=[i.__dict__ for i in scheduled],
             matching_interview_id=match.id if match else None)
    return page, match, other


def verify_interview(driver: RecruitAppDriver, log: ev.RunLog, req: InterviewRequest) -> StepOutcome:
    """Check the source of truth: exactly one scheduled interview, and it matches the request."""
    try:
        state = driver.source_of_truth()
    except AppUnavailable as exc:
        log.emit(ev.VERIFICATION, f"Could not verify: {exc}", passed=False)
        return StepOutcome(False, "Interview could not be verified", blocker=str(exc))
    scheduled = [i for i in state["interviews"]
                 if i["candidate_id"] == req.candidate_id and i["status"] == "scheduled"]
    matching = [i for i in scheduled if _matches(i, req)]
    passed = len(scheduled) == 1 and len(matching) == 1
    if passed:
        msg = (f"Verified in the app's records: exactly 1 scheduled interview for {req.candidate_name} "
               f"(#{matching[0]['id']}, {req.date} {req.start_time}, {req.interviewer}). No duplicates.")
    elif len(matching) > 1:
        msg = f"DUPLICATE DETECTED: {len(matching)} matching interviews for {req.candidate_name}."
    else:
        msg = (f"Expected 1 matching scheduled interview for {req.candidate_name}; found "
               f"{len(matching)} matching out of {len(scheduled)} scheduled.")
    log.emit(ev.VERIFICATION, msg, passed=passed, scheduled_count=len(scheduled),
             matching_count=len(matching), interview_ids=[i["id"] for i in scheduled])
    if not passed:
        return StepOutcome(False, msg, blocker=msg)
    return StepOutcome(True, msg, interview_id=matching[0]["id"])


def schedule_interview(driver: RecruitAppDriver, log: ev.RunLog, req: InterviewRequest, *,
                       max_attempts: int = 3, backoff_seconds: float = 1.0) -> StepOutcome:
    # 1. Pre-action state check: makes the step idempotent across re-runs.
    try:
        _, match, other = _check_state(driver, log, req, "pre-action")
    except AppUnavailable as exc:
        blocker = f"Cannot open the candidate page, so the current state is unknown: {exc}"
        return StepOutcome(False, "Could not read current state", blocker=blocker)
    if match:
        log.emit(ev.SKIPPED, f"Interview #{match.id} already exists; not creating another.",
                 interview_id=match.id)
        out = verify_interview(driver, log, req)
        out.summary = f"Already scheduled (#{match.id}); no new interview created. " + out.summary
        return out
    if other:
        blocker = (f"{req.candidate_name} already has a different interview scheduled "
                   f"(#{other.id}, {other.date} {other.start_time} with {other.interviewer}). "
                   "Rescheduling would cancel it, which exceeds this goal's authority; needs your approval.")
        return StepOutcome(False, "Conflicting interview exists", blocker=blocker)

    # 2. Attempt, and on failure recover based on observed state, never blindly.
    attempt = 0
    last: SubmitResult | None = None
    while attempt < max_attempts:
        attempt += 1
        if attempt == 1:
            log.emit(ev.ACTION, f"Submitting the schedule-interview form: {req.describe()}.",
                     attempt=attempt, max_attempts=max_attempts)
        last = driver.submit_interview(req.candidate_id, req.interviewer, req.date,
                                       req.start_time, req.duration_minutes)
        if last.ok:
            log.emit(ev.ACTION_SUCCEEDED, f"App accepted the interview (attempt {attempt}).",
                     attempt=attempt, http_status=last.http_status)
            out = verify_interview(driver, log, req)
            out.attempts, out.created_by_agent = attempt, True
            if out.ok and attempt > 1:
                out.summary = f"Recovered after {attempt - 1} failed attempt(s). " + out.summary
            return out

        log.emit(ev.ACTION_FAILED, f"Attempt {attempt} failed: {last.message}",
                 attempt=attempt, http_status=last.http_status, error=last.message,
                 transient=last.transient, outcome_known=False)

        # 3. Did the failed request actually create the interview?
        try:
            _, match, other = _check_state(driver, log, req, "post-failure")
        except AppUnavailable as exc:
            blocker = (f"Attempt {attempt} failed ({last.message}) and the candidate page cannot be read "
                       f"({exc}). Not retrying: the interview may already exist and a blind retry "
                       "could create a duplicate. Check the calendar manually.")
            return StepOutcome(False, "Outcome of failed action is unknown", attempts=attempt, blocker=blocker)

        if match:
            log.emit(ev.RECOVERY_STARTED,
                     f"The failed request actually created interview #{match.id}. "
                     "Treating the action as done; NOT retrying.",
                     decision="no_retry", reason="action_already_applied", interview_id=match.id)
            out = verify_interview(driver, log, req)
            out.attempts, out.created_by_agent = attempt, True
            if out.ok:
                out.summary = (f"Recovered: the app reported '{last.message}', but the interview was saved. "
                               "No retry, no duplicate. " + out.summary)
            return out
        if other:
            blocker = (f"Attempt failed ({last.message}) and {req.candidate_name} now has a different "
                       f"interview (#{other.id}). Stopping so a person can review.")
            return StepOutcome(False, "Unexpected interview found", attempts=attempt, blocker=blocker)
        if not last.transient:
            blocker = (f"The app rejected the request: {last.message} (HTTP {last.http_status}). "
                       "Retrying would not help; the goal needs different details.")
            log.emit(ev.RECOVERY_STARTED, "Interview not created and the error is permanent; not retrying.",
                     decision="stop", reason="permanent_error", http_status=last.http_status)
            return StepOutcome(False, "Request rejected by the app", attempts=attempt, blocker=blocker)
        if attempt >= max_attempts:
            break

        delay = backoff_seconds * attempt  # linear backoff
        log.emit(ev.RECOVERY_STARTED,
                 f"Confirmed the interview was NOT created and the error is transient; "
                 f"retrying in {delay:g}s ({max_attempts - attempt} attempt(s) left).",
                 decision="retry", reason="not_applied_and_transient", backoff_seconds=delay)
        time.sleep(delay)
        log.emit(ev.RETRY, f"Retry {attempt}: resubmitting the schedule-interview form.",
                 attempt=attempt + 1, max_attempts=max_attempts)

    blocker = (f"Calendar service still failing after {attempt} attempts (last error: {last.message}). "
               f"Verified that no interview was created for {req.candidate_name}, so it is safe to "
               "re-run this goal once the calendar service recovers.")
    return StepOutcome(False, "Retries exhausted", attempts=attempt, blocker=blocker)


def ensure_status(driver: RecruitAppDriver, log: ev.RunLog, candidate_id: int, status: str) -> StepOutcome:
    """Idempotent status update: check, act only if needed, then verify."""
    try:
        page = driver.read_candidate(candidate_id)
    except AppUnavailable as exc:
        return StepOutcome(False, "Could not read candidate", blocker=str(exc))
    log.emit(ev.STATE_CHECK, f"{page.name}'s status is '{page.status}'.", phase="pre-action",
             candidate_status=page.status)
    if page.status == status:
        log.emit(ev.SKIPPED, f"Status is already '{status}'; nothing to change.")
    else:
        log.emit(ev.ACTION, f"Updating {page.name}'s status from '{page.status}' to '{status}'.")
        res = driver.update_status(candidate_id, status)
        if not res.ok:
            log.emit(ev.ACTION_FAILED, f"Status update failed: {res.message}", http_status=res.http_status)
            # Setting a status is idempotent, but still check before deciding anything.
            page = driver.read_candidate(candidate_id)
            log.emit(ev.STATE_CHECK, f"Status is now '{page.status}'.", phase="post-failure",
                     candidate_status=page.status)
            if page.status != status:
                return StepOutcome(False, "Status not updated", blocker=f"Could not set status: {res.message}")
        else:
            log.emit(ev.ACTION_SUCCEEDED, "Status update accepted.")
    try:
        state = driver.source_of_truth()
    except AppUnavailable as exc:
        log.emit(ev.VERIFICATION, f"Could not verify: {exc}", passed=False)
        return StepOutcome(False, "Status could not be verified", blocker=str(exc))
    actual = next(c["status"] for c in state["candidates"] if c["id"] == candidate_id)
    passed = actual == status
    log.emit(ev.VERIFICATION, f"Candidate status in the app's records: '{actual}'.",
             passed=passed, expected=status, actual=actual)
    return StepOutcome(passed, f"Status is '{actual}'.",
                       blocker=None if passed else f"Status is '{actual}', expected '{status}'.")
