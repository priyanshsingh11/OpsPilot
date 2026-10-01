"""Sending the interview invitation: the one action in this workflow that needs the user's approval.

Sending an email to a candidate cannot be undone, so the agent first shows the user exactly what
would be sent and waits. The send step itself is gated in code (see control.ApprovalGrant), and
follows the same discipline as scheduling: check state first, never blindly repeat a send.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from . import events as ev
from .control import RunControl
from .driver import AppUnavailable, BrowserDriver, CandidatePage, HarnessClient
from .scheduling import InterviewRequest

ACTION = "send_invitation"


@dataclass
class InvitationOutcome:
    status: str  # sent | already_sent | rejected | failed
    summary: str
    blocker: str | None = None
    invitation_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("sent", "already_sent")


def compose(req: InterviewRequest, candidate_email: str, job_title: str, interview_id: str) -> dict:
    """The exact email, built from facts the agent read in the app. This is what the user approves."""
    when = datetime.fromisoformat(req.scheduled_at)
    first = req.candidate_name.split()[0]
    return {
        "candidate_id": req.candidate_id,
        "interview_id": interview_id,
        "to": candidate_email,
        "subject": f"Interview invitation: {req.round_name} for {job_title or 'the open role'}",
        "message": (
            f"Hi {first},\n\n"
            f"We'd like to invite you to a {req.round_name} for the {job_title or 'open'} role on "
            f"{when:%A, %d %B %Y} at {when:%H:%M} with {req.interviewer}.\n\n"
            "Please reply to this email to confirm your availability.\n\n"
            "Thanks,\nThe Recruiting Team"
        ),
    }


def approval_details(req: InterviewRequest, page: CandidatePage, payload: dict) -> dict:
    """What the user is shown: the action, who is affected, the content, and the consequences."""
    return {
        "title": "Approval Required: send interview invitation",
        "action": f"Send an interview invitation email to {req.candidate_name}.",
        "candidates": [{"id": req.candidate_id, "name": req.candidate_name, "email": page.email,
                        "status": page.status}],
        "email": {"to": payload["to"], "subject": payload["subject"], "message": payload["message"]},
        "if_approved": (f"The email above is sent to {page.email} through the recruitment app. "
                        "It cannot be recalled. The agent then verifies the invitation was recorded."),
        "if_rejected": (f"No email is sent. The interview for {req.candidate_name} stays scheduled and "
                        "the run ends, with the invitation marked as not sent."),
    }


def _already_sent(page: CandidatePage, interview_id: str):
    return next((v for v in page.invitations if v.interview_id == interview_id and v.status == "sent"), None)


def verify_invitation(harness: HarnessClient, log: ev.RunLog, payload: dict,
                      check_content: bool = True) -> InvitationOutcome:
    """Check the app's records: exactly one invitation for this interview (with the approved content)."""
    try:
        state = harness.source_of_truth()
    except AppUnavailable as exc:
        log.emit(ev.VERIFICATION, f"Could not verify the invitation: {exc}", passed=False)
        return InvitationOutcome("failed", "Invitation could not be verified", blocker=str(exc))
    mine = [v for v in state["invitations"]
            if v["candidate_id"] == payload["candidate_id"] and v["interview_id"] == payload["interview_id"]]
    exact = [v for v in mine if v["to"] == payload["to"] and v["subject"] == payload["subject"]
             and v["message"] == payload["message"].strip()]
    passed = len(mine) == 1 and (len(exact) == 1 or not check_content)
    if passed:
        msg = (f"Verified in the app's records: exactly 1 invitation ({mine[0]['id']}) to {payload['to']} "
               "for this interview" + (", with the approved subject and message." if check_content else "."))
    elif len(mine) > 1:
        msg = f"DUPLICATE DETECTED: {len(mine)} invitations recorded for this interview."
    else:
        msg = f"Expected 1 invitation with the approved content; found {len(mine)} ({len(exact)} exact)."
    log.emit(ev.VERIFICATION, msg, passed=passed, invitation_ids=[v["id"] for v in mine])
    if not passed:
        return InvitationOutcome("failed", msg, blocker=msg)
    return InvitationOutcome("sent", msg, invitation_id=mine[0]["id"])


def send_invitation_with_approval(driver: BrowserDriver, harness: HarnessClient, log: ev.RunLog,
                                  control: RunControl, req: InterviewRequest, interview_id: str, *,
                                  max_attempts: int = 3, backoff_seconds: float = 1.0) -> InvitationOutcome:
    control.checkpoint()
    try:
        page = driver.read_candidate(req.candidate_id)
    except AppUnavailable as exc:
        return InvitationOutcome("failed", "Could not read candidate", blocker=(
            f"Cannot open the candidate page, so it is unknown whether an invitation was already sent: {exc}"))
    sent = _already_sent(page, interview_id)
    log.emit(ev.STATE_CHECK, f"Opened {page.name}'s candidate page: "
             + (f"invitation {sent.id} was already sent for this interview."
                if sent else "no invitation has been sent for this interview yet."),
             phase="pre-action", invitations_on_page=[v.__dict__ for v in page.invitations])
    payload = compose(req, page.email, page.job_title, interview_id)
    if sent:
        log.emit(ev.SKIPPED, f"Invitation {sent.id} already sent; not sending another, and no approval needed.")
        out = verify_invitation(harness, log, payload, check_content=False)
        out.status = "already_sent" if out.ok else out.status
        return out

    # The gate. Nothing below runs unless the user approves exactly this email.
    control.save(steps={"send_invitation": "awaiting_approval"})
    grant = control.request_approval(ACTION, payload, approval_details(req, page, payload))
    if grant is None:
        control.save(steps={"send_invitation": "rejected"})
        return InvitationOutcome("rejected", f"You rejected sending the invitation to {req.candidate_name}.")
    control.checkpoint()  # a stop issued while approving still wins

    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        log.emit(ev.ACTION, f"Sending the approved invitation to {payload['to']} via the invitation form.",
                 attempt=attempt, approval_id=grant.approval_id)
        res = driver.send_invitation(grant, payload)
        if res.ok:
            log.emit(ev.ACTION_SUCCEEDED, f"App accepted the invitation (attempt {attempt}).",
                     attempt=attempt, screenshot=res.screenshot)
            break
        log.emit(ev.ACTION_FAILED, f"Sending failed (attempt {attempt}): {res.message}",
                 attempt=attempt, error_type=res.error_type, transient=res.transient, screenshot=res.screenshot)
        try:  # did the failed request send it anyway?
            page = driver.read_candidate(req.candidate_id)
        except AppUnavailable as exc:
            return InvitationOutcome("failed", "Outcome of failed send is unknown", blocker=(
                f"Sending failed ({res.message}) and the page cannot be read ({exc}). Not retrying: the "
                "email may have been sent, and a blind retry could send it twice."))
        sent = _already_sent(page, interview_id)
        log.emit(ev.STATE_CHECK, "Re-opened the candidate page: "
                 + (f"invitation {sent.id} exists, so the failed request did send it." if sent
                    else "no invitation was recorded."), phase="post-failure")
        if sent:
            log.emit(ev.RECOVERY_STARTED, "The failed request actually sent the invitation; NOT retrying.",
                     decision="no_retry", reason="action_already_applied")
            break
        if not res.transient or attempt >= max_attempts:
            return InvitationOutcome("failed", "Invitation not sent", blocker=(
                f"The invitation was not sent: {res.message}. Verified that nothing was recorded, "
                "so it is safe to try again."))
        log.emit(ev.RECOVERY_STARTED, "Confirmed nothing was sent and the error is transient; retrying.",
                 decision="retry", reason="not_applied_and_transient")
        time.sleep(backoff_seconds * attempt)
        control.checkpoint()
        log.emit(ev.RETRY, f"Retry {attempt}: resubmitting the invitation form.", attempt=attempt + 1)

    out = verify_invitation(harness, log, payload)
    control.save(steps={"send_invitation": "sent" if out.ok else "failed"})
    return out
