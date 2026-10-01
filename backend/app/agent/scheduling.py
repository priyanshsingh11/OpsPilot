"""State-aware interview creation with safe recovery.

Creating an interview is not idempotent: a failed request may or may not have
written the interview (the calendar can save it and still time out), and the
app itself does not reject duplicates. So a failure never triggers a blind retry:

    attempt -> failure -> inspect current state -> did the action actually happen?
        yes                    -> do not retry; verify
        no, failure transient  -> retry (bounded), then verify
        no, failure permanent  -> stop with a concrete blocker
        state unreadable       -> stop; retrying blind could create a duplicate

The same state check runs before the first attempt, so re-running a goal that
already succeeded creates nothing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from . import events as ev
from .driver import AppUnavailable, BrowserDriver, CandidatePage, HarnessClient, InterviewRow


@dataclass
class InterviewRequest:
    candidate_id: str
    candidate_name: str
    round_name: str
    scheduled_at: str  # YYYY-MM-DDTHH:MM
    interviewer: str

    def describe(self) -> str:
        when = datetime.fromisoformat(self.scheduled_at).strftime("%Y-%m-%d at %H:%M")
        return f"'{self.round_name}' for {self.candidate_name} with {self.interviewer} on {when}"


@dataclass
class StepOutcome:
    ok: bool
    summary: str
    interview_id: str | None = None
    attempts: int = 0
    created_by_agent: bool = False  # False when it already existed before this run
    blocker: str | None = None


def _same_time(a: str, b: str) -> bool:
    try:
        return datetime.fromisoformat(a) == datetime.fromisoformat(b)
    except ValueError:
        return a == b


def _field(row, name: str):
    return row[name] if isinstance(row, dict) else getattr(row, name)


def _same_round(row, req: InterviewRequest) -> bool:
    return (_field(row, "status") == "scheduled"
            and _field(row, "round").strip().lower() == req.round_name.strip().lower())


def _matches(row, req: InterviewRequest) -> bool:
    return (_same_round(row, req) and _same_time(_field(row, "scheduled_at"), req.scheduled_at)
            and _field(row, "interviewer").strip().lower() == req.interviewer.strip().lower())


def _check_state(driver: BrowserDriver, log: ev.RunLog, req: InterviewRequest, phase: str):
    """Open the candidate page in the browser and classify what is there.

    Returns (page, match, conflict): `match` is a scheduled interview identical to the
    request; `conflict` is the same round booked with a different time or interviewer.
    """
    page: CandidatePage = driver.read_candidate(req.candidate_id)
    same_round = [i for i in page.interviews if _same_round(i, req)]
    match = next((i for i in same_round if _matches(i, req)), None)
    conflict = next((i for i in same_round if i is not match), None)
    if match:
        finding = f"interview {match.id} matching the request already exists"
    elif conflict:
        finding = (f"no exact match, but '{conflict.round}' is already booked "
                   f"({conflict.scheduled_at} with {conflict.interviewer}, {conflict.id})")
    else:
        finding = f"no '{req.round_name}' interview exists"
    log.emit(ev.STATE_CHECK, f"[{phase}] Opened {page.name}'s candidate page in the browser: {finding}.",
             phase=phase, candidate_status=page.status,
             interviews_on_page=[i.__dict__ for i in page.interviews],
             matching_interview_id=match.id if match else None)
    return page, match, conflict


def verify_interview(harness: HarnessClient, log: ev.RunLog, req: InterviewRequest) -> StepOutcome:
    """Check the app's records: exactly one scheduled interview for this round, matching the request."""
    try:
        state = harness.source_of_truth()
    except AppUnavailable as exc:
        log.emit(ev.VERIFICATION, f"Could not verify: {exc}", passed=False)
        return StepOutcome(False, "Interview could not be verified", blocker=str(exc))
    same_round = [i for i in state["interviews"]
                  if i["candidate_id"] == req.candidate_id and _same_round(i, req)]
    matching = [i for i in same_round if _matches(i, req)]
    passed = len(same_round) == 1 and len(matching) == 1
    if passed:
        msg = (f"Verified in the app's records: exactly 1 scheduled '{req.round_name}' for "
               f"{req.candidate_name} ({matching[0]['id']}, {req.scheduled_at}, {req.interviewer}). "
               "No duplicates.")
    elif len(matching) > 1:
        msg = f"DUPLICATE DETECTED: {len(matching)} identical '{req.round_name}' interviews for {req.candidate_name}."
    else:
        msg = (f"Expected 1 matching '{req.round_name}' for {req.candidate_name}; found {len(matching)} "
               f"matching out of {len(same_round)} scheduled for that round.")
    log.emit(ev.VERIFICATION, msg, passed=passed, same_round_count=len(same_round),
             matching_count=len(matching), interview_ids=[i["id"] for i in same_round])
    if not passed:
        return StepOutcome(False, msg, blocker=msg)
    return StepOutcome(True, msg, interview_id=matching[0]["id"])


def schedule_interview(driver: BrowserDriver, harness: HarnessClient, log: ev.RunLog,
                       req: InterviewRequest, *, max_attempts: int = 3,
                       backoff_seconds: float = 1.0) -> StepOutcome:
    # 1. Pre-action state check: makes the step idempotent across re-runs.
    try:
        _, match, conflict = _check_state(driver, log, req, "pre-action")
    except AppUnavailable as exc:
        return StepOutcome(False, "Could not read current state",
                           blocker=f"Cannot open the candidate page, so the current state is unknown: {exc}")
    if match:
        log.emit(ev.SKIPPED, f"Interview {match.id} already exists; not creating another.",
                 interview_id=match.id)
        out = verify_interview(harness, log, req)
        out.summary = f"Already scheduled ({match.id}); no new interview created. " + out.summary
        return out
    if conflict:
        return StepOutcome(False, "Round already booked", blocker=(
            f"{req.candidate_name} already has '{conflict.round}' booked for {conflict.scheduled_at} with "
            f"{conflict.interviewer} ({conflict.id}). Rescheduling it exceeds this goal's authority; "
            "needs your approval."))

    # 2. Attempt; on failure, recover based on observed state, never blindly.
    attempt, last = 0, None
    while attempt < max_attempts:
        attempt += 1
        if attempt == 1:
            log.emit(ev.ACTION, f"Filling and submitting the Schedule Interview form: {req.describe()}.",
                     attempt=attempt, max_attempts=max_attempts)
        last = driver.submit_interview(req.candidate_id, req.round_name, req.scheduled_at, req.interviewer)
        if last.ok:
            log.emit(ev.ACTION_SUCCEEDED, f"App accepted the interview (attempt {attempt}).",
                     attempt=attempt, screenshot=last.screenshot)
            out = verify_interview(harness, log, req)
            out.attempts, out.created_by_agent = attempt, True
            if out.ok and attempt > 1:
                out.summary = f"Recovered after {attempt - 1} failed attempt(s). " + out.summary
            return out

        log.emit(ev.ACTION_FAILED, f"Attempt {attempt} failed: {last.message}",
                 attempt=attempt, error_type=last.error_type, http_status=last.http_status,
                 transient=last.transient, outcome_known=False, screenshot=last.screenshot)

        # 3. Did the failed request actually create the interview?
        try:
            _, match, conflict = _check_state(driver, log, req, "post-failure")
        except AppUnavailable as exc:
            return StepOutcome(False, "Outcome of failed action is unknown", attempts=attempt, blocker=(
                f"Attempt {attempt} failed ({last.message}) and the candidate page cannot be read ({exc}). "
                "Not retrying: the interview may already exist and a blind retry could create a "
                "duplicate. Check the calendar manually."))

        if match:
            log.emit(ev.RECOVERY_STARTED,
                     f"The failed request actually created interview {match.id}. "
                     "Treating the action as done; NOT retrying.",
                     decision="no_retry", reason="action_already_applied", interview_id=match.id)
            out = verify_interview(harness, log, req)
            out.attempts, out.created_by_agent = attempt, True
            if out.ok:
                out.summary = (f"Recovered: the app reported '{last.message}', but the interview was saved. "
                               "No retry, no duplicate. " + out.summary)
            return out
        if conflict:
            return StepOutcome(False, "Unexpected interview found", attempts=attempt, blocker=(
                f"Attempt failed ({last.message}) and '{conflict.round}' now exists with different details "
                f"({conflict.id}). Stopping so a person can review."))
        if not last.transient:
            log.emit(ev.RECOVERY_STARTED, "Interview not created and the error is permanent; not retrying.",
                     decision="stop", reason="permanent_error", error_type=last.error_type)
            return StepOutcome(False, "Request rejected by the app", attempts=attempt, blocker=(
                f"The app rejected the request: {last.message}. Retrying would not help; "
                "the goal needs different details."))
        if attempt >= max_attempts:
            log.emit(ev.RECOVERY_STARTED, f"Interview not created, but all {max_attempts} attempts are used; "
                     "stopping instead of retrying further.",
                     decision="stop", reason="retries_exhausted", attempts=attempt)
            break

        delay = backoff_seconds * attempt  # linear backoff
        log.emit(ev.RECOVERY_STARTED,
                 f"Confirmed the interview was NOT created and the error is transient; "
                 f"retrying in {delay:g}s ({max_attempts - attempt} attempt(s) left).",
                 decision="retry", reason="not_applied_and_transient", backoff_seconds=delay)
        time.sleep(delay)
        log.emit(ev.RETRY, f"Retry {attempt}: resubmitting the Schedule Interview form.",
                 attempt=attempt + 1, max_attempts=max_attempts)

    return StepOutcome(False, "Retries exhausted", attempts=attempt, blocker=(
        f"Interview service still failing after {attempt} attempts (last error: {last.message}). "
        f"Verified that no '{req.round_name}' was created for {req.candidate_name}, so it is safe to "
        "re-run this goal once the service recovers."))


def ensure_status(driver: BrowserDriver, harness: HarnessClient, log: ev.RunLog,
                  candidate_id: str, status: str) -> StepOutcome:
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
        if res.ok:
            log.emit(ev.ACTION_SUCCEEDED, res.message, screenshot=res.screenshot)
        else:
            log.emit(ev.ACTION_FAILED, f"Status update failed: {res.message}", error_type=res.error_type)
            # Setting a status is idempotent, but still check before deciding anything.
            page = driver.read_candidate(candidate_id)
            log.emit(ev.STATE_CHECK, f"Status is now '{page.status}'.", phase="post-failure",
                     candidate_status=page.status)
            if page.status != status:
                return StepOutcome(False, "Status not updated", blocker=f"Could not set status: {res.message}")
    try:
        state = harness.source_of_truth()
    except AppUnavailable as exc:
        log.emit(ev.VERIFICATION, f"Could not verify: {exc}", passed=False)
        return StepOutcome(False, "Status could not be verified", blocker=str(exc))
    actual = next(c["status"] for c in state["candidates"] if c["id"] == candidate_id)
    passed = actual == status
    log.emit(ev.VERIFICATION, f"Candidate status in the app's records: '{actual}'.",
             passed=passed, expected=status, actual=actual)
    return StepOutcome(passed, f"Status is '{actual}'.",
                       blocker=None if passed else f"Status is '{actual}', expected '{status}'.")
