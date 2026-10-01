"""In-memory data store for the synthetic recruitment application.

State lives in process memory and is re-seeded on every start. The store also
owns the failure simulation for the interview service:

- chaos_mode: every create fails until switched off (outage).
- failure plan: the next N creates fail deterministically, either
  "before_write" (nothing saved) or "after_write" (the interview IS saved, but
  the request still fails, like a calendar sync that times out after commit).
  The plan can be armed from the environment (DEMO_FAILING_ATTEMPTS,
  DEMO_FAILURE_MODE) or via POST /admin/failure, so a demo is reproducible.
"""

import copy
import itertools
import os
from datetime import datetime

from .models import CANDIDATES, INTERVIEWS, JOBS, Candidate, Interview


FAILURE_MODES = ("before_write", "after_write")


class Store:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Re-seed all data and re-arm the failure plan from the environment."""
        # Deep copies so status updates never mutate the module-level seed objects.
        self.jobs: dict[str, dict] = {j.id: copy.deepcopy(j.__dict__) for j in JOBS}
        self.candidates: dict[str, dict] = {c.id: copy.deepcopy(c.__dict__) for c in CANDIDATES}
        self.interviews: dict[str, dict] = {i.id: copy.deepcopy(i.__dict__) for i in INTERVIEWS}
        self._interview_counter = itertools.count(1)
        self.invitations: dict[str, dict] = {}
        self._invitation_counter = itertools.count(1)
        # When True, creating an interview fails with a 500 (simulated outage).
        self.chaos_mode: bool = False
        mode = os.getenv("DEMO_FAILURE_MODE", "before_write").strip()
        self.set_failure_plan(int(os.getenv("DEMO_FAILING_ATTEMPTS", "0") or 0),
                              mode if mode in FAILURE_MODES else "before_write")

    # --- Failure simulation ---
    def set_failure_plan(self, remaining: int, mode: str) -> None:
        if mode not in FAILURE_MODES:
            raise ValueError(f"Unknown failure mode '{mode}'")
        self.failure_remaining = max(remaining, 0)
        self.failure_mode = mode

    def failure_state(self) -> dict:
        return {"chaos_mode": self.chaos_mode, "remaining": self.failure_remaining,
                "mode": self.failure_mode}

    # --- Jobs ---
    def list_jobs(self) -> list[dict]:
        return list(self.jobs.values())

    def get_job(self, job_id: str) -> dict | None:
        return self.jobs.get(job_id)

    # --- Candidates ---
    def list_candidates(self, job_id: str) -> list[dict]:
        return [c for c in self.candidates.values() if c["job_id"] == job_id]

    def list_candidates_all(self) -> list[dict]:
        return list(self.candidates.values())

    def get_candidate(self, candidate_id: str) -> dict | None:
        return self.candidates.get(candidate_id)

    def update_candidate_status(self, candidate_id: str, status: str) -> dict | None:
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            return None
        candidate["status"] = status
        return candidate

    # --- Interviews ---
    def list_interviews(self) -> list[dict]:
        return list(self.interviews.values())

    def interviews_for_candidate(self, candidate_id: str) -> list[dict]:
        return [i for i in self.interviews.values() if i["candidate_id"] == candidate_id]

    def create_interview(
        self,
        candidate_id: str,
        job_id: str,
        round_name: str,
        scheduled_at: str,
        interviewer: str,
    ) -> Interview:
        """Create an interview, applying chaos mode and the armed failure plan.

        Raises InterviewServiceError on a simulated failure. In "after_write"
        mode the interview is stored before the error is raised.
        """
        if self.chaos_mode:
            raise InterviewServiceError("Interview service unavailable (simulated outage)")
        planned_failure = self.failure_remaining > 0
        if planned_failure:
            self.failure_remaining -= 1
            if self.failure_mode == "before_write":
                raise InterviewServiceError(
                    "Interview service unavailable (simulated). The interview was not created.")
        interview = Interview(
            id=f"int-{next(self._interview_counter)}",
            candidate_id=candidate_id,
            job_id=job_id,
            round=round_name,
            scheduled_at=scheduled_at,
            interviewer=interviewer,
        )
        self.interviews[interview.id] = interview.__dict__
        if planned_failure:  # after_write: saved, but the caller is told it failed
            raise InterviewServiceError("Calendar sync timed out (simulated). Please check the calendar.")
        return interview


    # --- Invitations ---
    def invitations_for_candidate(self, candidate_id: str) -> list[dict]:
        return [i for i in self.invitations.values() if i["candidate_id"] == candidate_id]

    def send_invitation(self, candidate_id: str, interview_id: str, subject: str, message: str) -> dict:
        """Record an invitation email as sent. Like the real mail service it does not dedupe:
        sending twice sends twice, so callers must check before sending."""
        candidate = self.candidates.get(candidate_id)
        interview = self.interviews.get(interview_id)
        if candidate is None:
            raise ValueError("Candidate not found")
        if interview is None or interview["candidate_id"] != candidate_id or interview["status"] != "scheduled":
            raise ValueError("Choose one of this candidate's scheduled interviews")
        if not subject.strip() or not message.strip():
            raise ValueError("Subject and message are required")
        invitation = {
            "id": f"inv-{next(self._invitation_counter)}",
            "candidate_id": candidate_id,
            "interview_id": interview_id,
            "to": candidate["email"],
            "subject": subject.strip(),
            "message": message.replace("\r\n", "\n").strip(),  # browsers submit CRLF
            "status": "sent",
            "sent_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.invitations[invitation["id"]] = invitation
        return invitation


class InterviewServiceError(Exception):
    """Raised when the (simulated) interview service fails."""


store = Store()
