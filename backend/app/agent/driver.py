"""How the agent touches the synthetic recruitment app (demo-app/app).

- BrowserDriver: every read and action the agent performs goes through a real
  Chromium browser via the Phase 3 automation layer (automation/RecruitmentBrowser).
  It converts that layer's ActionResults into the small shapes the recovery logic needs.
- HarnessClient: plain HTTP, used for two things only: the demo harness (reset
  data, arm a failure) and independent verification against /api/state.
  It is never used to perform the goal's actions.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .control import ApprovalGrant, ApprovalRequired

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))  # makes the top-level `automation` package importable

# Screenshots (evidence) live under here, one folder per run; served by the backend at /evidence.
SCREENSHOT_ROOT = ROOT_DIR / "screenshots"

# Failure categories from automation/errors.py that may succeed on a later attempt.
TRANSIENT_ERRORS = {"server_error", "timeout", "browser", "navigation", "unknown"}


class AppUnavailable(Exception):
    """The app could not be read, so the current state is unknown."""


@dataclass
class CandidateRef:
    id: str
    name: str
    status: str
    job_id: str


@dataclass
class InterviewRow:
    id: str
    round: str
    scheduled_at: str
    interviewer: str
    status: str
    duration_minutes: int | None = None


@dataclass
class InvitationRow:
    id: str
    interview_id: str
    to: str
    subject: str
    status: str


@dataclass
class CandidatePage:
    id: str
    name: str
    status: str
    interviews: list[InterviewRow]
    invitations: list[InvitationRow] = field(default_factory=list)
    email: str = ""
    job_title: str = ""
    screenshot: str | None = None


@dataclass
class JobRef:
    id: str
    title: str
    hiring_manager: str


@dataclass
class CalendarRow:
    candidate_id: str
    candidate_name: str
    round: str
    scheduled_at: str
    interviewer: str
    status: str
    duration_minutes: int = 60


@dataclass
class SubmitResult:
    ok: bool
    message: str
    error_type: str | None = None  # automation/errors.py category
    http_status: str | None = None
    screenshot: str | None = None

    @property
    def transient(self) -> bool:
        return self.error_type in TRANSIENT_ERRORS


class BrowserDriver:
    def __init__(self, browser):
        self.browser = browser  # automation.RecruitmentBrowser (or a test double)

    @classmethod
    def launch(cls, base_url: str, headless: bool = True, screenshot_dir: str | Path | None = None) -> "BrowserDriver":
        from automation import RecruitmentBrowser

        browser = RecruitmentBrowser(base_url=base_url, headless=headless,
                                     screenshot_dir=str(screenshot_dir or SCREENSHOT_ROOT))
        browser.launch()
        return cls(browser)

    def close(self) -> None:
        self.browser.close()

    @staticmethod
    def _unwrap(result, what: str):
        if not result.success:
            raise AppUnavailable(f"{what} failed in the browser: {result.error.message}")
        return result.data

    def list_candidates(self) -> list[CandidateRef]:
        """Open the jobs page, then each job's candidate list."""
        jobs = self._unwrap(self.browser.open_jobs(), "Opening the jobs page")
        out: list[CandidateRef] = []
        for job in jobs:
            cands = self._unwrap(self.browser.filter_candidates(job.id), f"Opening {job.title} candidates")
            out += [CandidateRef(c.id, c.name, c.status, job.id) for c in cands]
        return out

    def list_jobs(self) -> list[JobRef]:
        jobs = self._unwrap(self.browser.open_jobs(), "Opening the jobs page")
        return [JobRef(j.id, j.title, j.hiring_manager) for j in jobs]

    def hiring_managers(self) -> list[str]:
        return sorted({j.hiring_manager for j in self.list_jobs()})

    def filter_candidates(self, job_id: str, status: str) -> list[CandidateRef]:
        """Open a job's candidate list and apply the status filter in the UI."""
        cands = self._unwrap(self.browser.filter_candidates(job_id, status=status), "Filtering candidates")
        return [CandidateRef(c.id, c.name, c.status, job_id) for c in cands]

    def list_interviews(self) -> tuple[list[CalendarRow], str | None]:
        """Open the interviews calendar page and read every row. Returns (rows, screenshot)."""
        session = self.browser.session
        try:
            session.goto("/interviews")
            session.page.wait_for_selector('[data-testid="interviews-table"]')
            rows = []
            for tr in session.page.query_selector_all('[data-testid="interviews-table"] tbody tr'):
                cells = tr.query_selector_all("td")
                if len(cells) < 6:
                    continue  # "No interviews scheduled." row
                link = cells[0].query_selector("a")
                href = link.get_attribute("href") if link else ""
                minutes = cells[6].inner_text().split()[0] if len(cells) > 6 else "60"
                rows.append(CalendarRow(href.rsplit("/", 1)[-1], *(c.inner_text().strip() for c in
                                        (cells[0], cells[2], cells[3], cells[4], cells[5])),
                                        int(minutes) if minutes.isdigit() else 60))
            return rows, session.screenshot("read_calendar")
        except Exception as exc:  # Playwright errors -> unknown state
            raise AppUnavailable(f"Reading the interviews calendar failed: {exc}") from exc

    def capture_page(self, path: str, label: str) -> str | None:
        """Open a page and save a full-page screenshot as evidence."""
        try:
            self.browser.session.goto(path)
            return self.browser.session.screenshot(label)
        except Exception:
            return None

    def read_candidate(self, candidate_id: str) -> CandidatePage:
        """Open the candidate's profile and read their status and interviews table."""
        details = self._unwrap(self.browser.get_candidate(candidate_id), "Opening the candidate page")
        # verify_interview re-reads the same page and returns every interview row.
        check_result = self.browser.verify_interview(candidate_id, "")
        check = self._unwrap(check_result, "Reading the interviews table")
        rows = [InterviewRow(i.id, i.round, i.scheduled_at, i.interviewer, i.status,
                             getattr(i, "duration_minutes", None))
                for i in check.scheduled_interviews]
        invites = self._unwrap(self.browser.get_invitations(candidate_id), "Reading the invitations table")
        return CandidatePage(
            candidate_id, details.name, details.status, rows,
            invitations=[InvitationRow(v.id, v.interview_id, v.to, v.subject, v.status) for v in invites],
            email=details.email, job_title=details.job_title, screenshot=check_result.screenshot)

    def submit_interview(self, candidate_id: str, round_name: str, scheduled_at: str,
                         interviewer: str, duration_minutes: int = 60) -> SubmitResult:
        r = self.browser.create_interview(candidate_id, round_name, scheduled_at, interviewer,
                                          duration_minutes=duration_minutes)
        if r.success:
            return SubmitResult(True, "Interview form accepted", screenshot=r.screenshot)
        return SubmitResult(False, r.error.message, r.error.type,
                            str(r.error.details.get("status_code")) if r.error.details else None,
                            r.screenshot)

    def send_invitation(self, grant: ApprovalGrant, payload: dict) -> SubmitResult:
        """Send the invitation email. Refuses unless the user's approval for exactly this payload
        is still in force, checked against the database at the moment of the action."""
        if not grant.authorizes("send_invitation", payload):
            raise ApprovalRequired("No valid user approval for this invitation; nothing was sent.")
        r = self.browser.send_invitation(payload["candidate_id"], payload["interview_id"],
                                         payload["subject"], payload["message"])
        if r.success:
            return SubmitResult(True, "Invitation form accepted", screenshot=r.screenshot)
        return SubmitResult(False, r.error.message, r.error.type,
                            str(r.error.details.get("status_code")) if r.error.details else None,
                            r.screenshot)

    def update_status(self, candidate_id: str, status: str) -> SubmitResult:
        r = self.browser.update_candidate_status(candidate_id, status)
        if r.success:
            return SubmitResult(True, f"Status changed to {r.data.new_status}", screenshot=r.screenshot)
        return SubmitResult(False, r.error.message, r.error.type, screenshot=r.screenshot)


class HarnessClient:
    def __init__(self, client: httpx.Client):
        self.client = client

    @classmethod
    def connect(cls, base_url: str, timeout: float = 5.0) -> "HarnessClient":
        return cls(httpx.Client(base_url=base_url, timeout=timeout, follow_redirects=False))

    def close(self) -> None:
        self.client.close()

    def source_of_truth(self) -> dict:
        try:
            r = self.client.get("/api/state")
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AppUnavailable(f"State API unavailable: {exc}") from exc

    def arm_failure(self, remaining: int, mode: str) -> None:
        self._post("/admin/failure", {"remaining": str(remaining), "mode": mode})

    def reset_demo_data(self) -> None:
        self._post("/admin/reset", {})

    def _post(self, path: str, data: dict) -> None:
        try:
            r = self.client.post(path, data=data)
        except httpx.HTTPError as exc:
            raise AppUnavailable(f"POST {path} failed: {exc}") from exc
        if r.status_code not in (302, 303):
            raise AppUnavailable(f"POST {path} returned HTTP {r.status_code}")
