"""In-memory data store for the synthetic recruitment application.

State lives in process memory and is re-seeded on every start. The store also
owns the "chaos mode" switch used to simulate an outage in the interview
service so the browser automation layer can be tested against failures.
"""

import itertools

from .models import CANDIDATES, INTERVIEWS, JOBS, Candidate, Interview


class Store:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {j.id: j.__dict__ for j in JOBS}
        self.candidates: dict[str, dict] = {c.id: c.__dict__ for c in CANDIDATES}
        self.interviews: dict[str, dict] = {i.id: i.__dict__ for i in INTERVIEWS}
        self._interview_counter = itertools.count(1)
        # When True, creating an interview fails with a 500 (simulated outage).
        self.chaos_mode: bool = False

    # --- Jobs ---
    def list_jobs(self) -> list[dict]:
        return list(self.jobs.values())

    def get_job(self, job_id: str) -> dict | None:
        return self.jobs.get(job_id)

    # --- Candidates ---
    def list_candidates(self, job_id: str) -> list[dict]:
        return [c for c in self.candidates.values() if c["job_id"] == job_id]

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
        """Create an interview. Raises InterviewServiceError when chaos mode is on."""
        if self.chaos_mode:
            raise InterviewServiceError("Interview service unavailable (simulated outage)")
        interview = Interview(
            id=f"int-{next(self._interview_counter)}",
            candidate_id=candidate_id,
            job_id=job_id,
            round=round_name,
            scheduled_at=scheduled_at,
            interviewer=interviewer,
        )
        self.interviews[interview.id] = interview.__dict__
        return interview


class InterviewServiceError(Exception):
    """Raised when the (simulated) interview service fails."""


store = Store()
