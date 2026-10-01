"""Reusable browser actions for the synthetic recruitment application.

Every action is a thin UI interaction: it navigates, clicks/fills, and
extracts structured data. Actions contain no recruitment business logic — they
return typed data (or structured errors) and let the caller decide what to do
next. All actions are order-independent: each one navigates to its own entry
point before interacting.
"""

import re
import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import errors
from .browser import BrowserSession
from .results import (
    ActionResult,
    CandidateDetails,
    CandidateSummary,
    InterviewDetails,
    InterviewVerification,
    JobSummary,
    StatusUpdate,
)


class RecruitmentBrowser:
    """Tool interface for operating the synthetic recruitment app.

    Conceptually:
        browser.open_jobs()
        browser.select_job(title="AI Engineer")
        browser.filter_candidates(job_id=..., status="shortlisted")
        browser.get_candidate(candidate_id=...)
        browser.create_interview(candidate_id=..., round_name=..., ...)
        browser.update_candidate_status(candidate_id=..., status=...)
        browser.verify_interview(candidate_id=..., round_name=...)
    """

    def __init__(
        self,
        base_url: str,
        headless: bool = True,
        screenshot_dir: str = "screenshots",
    ) -> None:
        self.session = BrowserSession(base_url=base_url, headless=headless, screenshot_dir=screenshot_dir)

    # --- Lifecycle ---

    def launch(self) -> None:
        self.session.launch()

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "RecruitmentBrowser":
        self.launch()
        return self

    def __exit__(self, *_exc) -> None:
        self.session.close()

    # --- Actions ---

    def open_jobs(self) -> ActionResult[list[JobSummary]]:
        """Open the jobs page and return all listed jobs."""
        return self._run("open_jobs", self._do_open_jobs)

    def select_job(self, title: str) -> ActionResult[JobSummary]:
        """Open the jobs page and navigate into the job with the given title."""
        return self._run("select_job", lambda: self._do_select_job(title))

    def filter_candidates(
        self,
        job_id: str,
        status: str | None = None,
        query: str | None = None,
    ) -> ActionResult[list[CandidateSummary]]:
        """Open a job's candidate list and apply the status/search filter."""
        return self._run(
            "filter_candidates",
            lambda: self._do_filter_candidates(job_id, status, query),
        )

    def get_candidate(self, candidate_id: str) -> ActionResult[CandidateDetails]:
        """Open a candidate's profile page and return their details."""
        return self._run("get_candidate", lambda: self._do_get_candidate(candidate_id))

    def create_interview(
        self,
        candidate_id: str,
        round_name: str,
        scheduled_at: str,
        interviewer: str,
    ) -> ActionResult[InterviewDetails]:
        """Schedule an interview for a candidate via the interview form.

        Detects server-side failure (e.g. simulated outage) and returns a
        structured error instead of raising.
        """
        return self._run(
            "create_interview",
            lambda: self._do_create_interview(candidate_id, round_name, scheduled_at, interviewer),
        )

    def update_candidate_status(self, candidate_id: str, status: str) -> ActionResult[StatusUpdate]:
        """Update a candidate's pipeline status via the status form."""
        return self._run(
            "update_candidate_status",
            lambda: self._do_update_candidate_status(candidate_id, status),
        )

    def verify_interview(self, candidate_id: str, round_name: str) -> ActionResult[InterviewVerification]:
        """Check whether an interview with the given round exists for a candidate."""
        return self._run(
            "verify_interview",
            lambda: self._do_verify_interview(candidate_id, round_name),
        )

    def toggle_chaos_mode(self, enabled: bool) -> ActionResult[bool]:
        """Toggle the app's simulated-outage switch (test/recovery helper)."""
        return self._run("toggle_chaos_mode", lambda: self._do_toggle_chaos_mode(enabled))

    def arm_failure_plan(self, remaining: int, mode: str = "before_write") -> ActionResult[dict]:
        """Arm a deterministic failure plan: the next `remaining` interview
        creates fail. Mode is "before_write" (nothing saved) or "after_write"
        (saved but the request still fails)."""
        return self._run(
            "arm_failure_plan",
            lambda: self._do_arm_failure_plan(remaining, mode),
        )

    def reset_state(self) -> ActionResult[bool]:
        """Reset the app to its seed state (test isolation helper)."""
        return self._run("reset_state", self._do_reset_state)

    # --- Action implementations ---

    def _do_open_jobs(self) -> list[JobSummary]:
        self.session.goto("/jobs")
        self.session.page.wait_for_selector('[data-testid="jobs-table"]')
        rows = self.session.page.query_selector_all('[data-testid="jobs-table"] tbody tr')
        jobs = []
        for row in rows:
            cells = row.query_selector_all("td")
            link = cells[0].query_selector("a")
            job_id = link.get_attribute("data-testid").removeprefix("job-link-")
            jobs.append(
                JobSummary(
                    id=job_id,
                    title=cells[0].inner_text().strip(),
                    department=cells[1].inner_text().strip(),
                    location=cells[2].inner_text().strip(),
                    hiring_manager=cells[3].inner_text().strip(),
                    status=cells[4].inner_text().strip(),
                    candidate_count=int(cells[5].inner_text().strip()),
                )
            )
        return jobs

    def _do_select_job(self, title: str) -> JobSummary:
        self.session.goto("/jobs")
        self.session.page.wait_for_selector('[data-testid="jobs-table"]')
        # Read the full summary from the jobs table before navigating in.
        jobs = self._do_open_jobs()
        match = next((j for j in jobs if j.title == title), None)
        if match is None:
            raise LookupError(f"Job '{title}' not found in jobs list")
        self.session.page.get_by_role("link", name=title, exact=True).click()
        self.session.page.wait_for_selector('[data-testid="job-title"]')
        return match

    def _do_filter_candidates(
        self, job_id: str, status: str | None, query: str | None
    ) -> list[CandidateSummary]:
        self.session.goto(f"/jobs/{job_id}")
        self.session.page.wait_for_selector('[data-testid="candidates-table"]')
        if status:
            self.session.page.select_option('[data-testid="filter-status"]', status)
        if query:
            self.session.page.fill('[data-testid="filter-query"]', query)
        self.session.page.click('[data-testid="filter-submit"]')
        self.session.page.wait_for_selector('[data-testid="candidates-table"]')
        # Wait for the filtered table to render (URL now carries the applied filter).
        if status:
            self.session.page.wait_for_url(re.compile(r"[?&]status="))
        if query:
            self.session.page.wait_for_url(re.compile(r"[?&]q="))
        rows = self.session.page.query_selector_all('[data-testid="candidates-table"] tbody tr')
        candidates = []
        for row in rows:
            cells = row.query_selector_all("td")
            if not cells or cells[0].get_attribute("data-testid") == "candidates-empty":
                break
            link = cells[0].query_selector("a")
            candidate_id = link.get_attribute("data-testid").removeprefix("candidate-link-")
            candidates.append(
                CandidateSummary(
                    id=candidate_id,
                    name=cells[0].inner_text().strip(),
                    email=cells[1].inner_text().strip(),
                    job_id=job_id,
                    status=cells[4].inner_text().strip(),
                    experience_years=int(cells[2].inner_text().strip().split()[0]),
                    skills=[s.strip() for s in cells[3].inner_text().split(",") if s.strip()],
                )
            )
        return candidates

    def _do_get_candidate(self, candidate_id: str) -> CandidateDetails:
        self.session.goto(f"/candidates/{candidate_id}")
        self.session.page.wait_for_selector('[data-testid="candidate-name"]')
        job = self.session.page.query_selector('[data-testid="candidate-summary"]')
        return CandidateDetails(
            id=candidate_id,
            name=self.session.page.text_content('[data-testid="candidate-name"]').strip(),
            email=self.session.page.text_content('[data-testid="candidate-email"]').strip(),
            job_id=self._current_job_id(),
            job_title=self._job_title_from_breadcrumb(),
            status=self.session.page.text_content('[data-testid="candidate-current-status"]').strip(),
            experience_years=int(
                self.session.page.text_content('[data-testid="candidate-experience"]').strip().split()[0]
            ),
            skills=[
                s.strip()
                for s in self.session.page.text_content('[data-testid="candidate-skills"]').strip().split(",")
                if s.strip()
            ],
            summary=job.inner_text().strip() if job else "",
        )

    def _do_create_interview(
        self, candidate_id: str, round_name: str, scheduled_at: str, interviewer: str
    ) -> InterviewDetails:
        self.session.goto(f"/candidates/{candidate_id}")
        self.session.page.wait_for_selector('[data-testid="schedule-interview-link"]')
        self.session.page.click('[data-testid="schedule-interview-link"]')
        self.session.page.wait_for_selector('[data-testid="interview-form"]')

        self.session.page.fill('[data-testid="interview-round"]', round_name)
        self.session.page.fill('[data-testid="interview-scheduled-at"]', scheduled_at)
        self.session.page.fill('[data-testid="interview-interviewer"]', interviewer)
        self.session.page.click('[data-testid="interview-submit"]')

        # The form either redirects back to the candidate page (success) or
        # renders an error page (simulated outage / validation failure).
        self.session.page.wait_for_load_state("domcontentloaded")
        error_banner = self.session.page.query_selector('[data-testid="error-banner"]')
        if error_banner is not None:
            status_el = self.session.page.query_selector('[data-testid="error-status"]')
            status_code = status_el.inner_text().strip() if status_el else "unknown"
            raise _ServerActionError(
                message=error_banner.inner_text().strip(),
                status_code=status_code,
            )

        self.session.page.wait_for_selector('[data-testid="flash-message"]')
        interviews = self._extract_interviews_from_candidate_page()
        for interview in interviews:
            if interview.round == round_name:
                return interview
        raise LookupError(
            f"Interview '{round_name}' was not found on the candidate page after creation"
        )

    def _do_update_candidate_status(self, candidate_id: str, status: str) -> StatusUpdate:
        self.session.goto(f"/candidates/{candidate_id}")
        self.session.page.wait_for_selector('[data-testid="status-form"]')
        previous = self.session.page.text_content('[data-testid="candidate-current-status"]').strip()
        self.session.page.select_option('[data-testid="status-select"]', status)
        self.session.page.click('[data-testid="status-submit"]')
        self.session.page.wait_for_selector('[data-testid="flash-message"]')
        new_status = self.session.page.text_content('[data-testid="candidate-current-status"]').strip()
        return StatusUpdate(
            candidate_id=candidate_id,
            previous_status=previous,
            new_status=new_status,
        )

    def _do_verify_interview(self, candidate_id: str, round_name: str) -> InterviewVerification:
        self.session.goto(f"/candidates/{candidate_id}")
        self.session.page.wait_for_selector('[data-testid="candidate-name"]')
        interviews = self._extract_interviews_from_candidate_page()
        matching = [i for i in interviews if i.round == round_name]
        return InterviewVerification(
            candidate_id=candidate_id,
            round_name=round_name,
            found=bool(matching),
            interview=matching[0] if matching else None,
            scheduled_interviews=interviews,
        )

    def _do_toggle_chaos_mode(self, enabled: bool) -> bool:
        self.session.goto("/interviews")
        self.session.page.wait_for_selector('[data-testid="chaos-form"]')
        current = self.session.page.text_content('[data-testid="chaos-state"]')
        is_on = "ON" in current.upper()
        if is_on != enabled:
            self.session.page.click('[data-testid="chaos-toggle"]')
            self.session.page.wait_for_selector('[data-testid="chaos-state"]')
        return enabled

    def _do_arm_failure_plan(self, remaining: int, mode: str) -> dict:
        self.session.goto("/interviews")
        self.session.page.wait_for_selector('[data-testid="failure-plan"]')
        # The failure plan is armed via the /admin/failure endpoint. It is a
        # plain POST form; submit it directly and read back the armed state.
        self.session.page.request.post(
            self.session.url_for("/admin/failure"),
            form={"remaining": str(remaining), "mode": mode},
        )
        self.session.goto("/interviews")
        self.session.page.wait_for_selector('[data-testid="failure-plan"]')
        remaining_text = self.session.page.text_content('[data-testid="failure-remaining"]').strip()
        mode_text = self.session.page.text_content('[data-testid="failure-mode"]').strip()
        return {"remaining": int(remaining_text), "mode": mode_text}

    def _do_reset_state(self) -> bool:
        self.session.page.request.post(self.session.url_for("/admin/reset"))
        self.session.goto("/jobs")
        self.session.page.wait_for_selector('[data-testid="jobs-table"]')
        return True

    # --- Extraction helpers ---

    def _extract_interviews_from_candidate_page(self) -> list[InterviewDetails]:
        rows = self.session.page.query_selector_all('[data-testid="interviews-table"] tbody tr')
        interviews = []
        for row in rows:
            cells = row.query_selector_all("td")
            if not cells or cells[0].get_attribute("data-testid") == "interviews-empty":
                break
            round_cell = cells[0].get_attribute("data-testid") or ""
            match = re.match(r"interview-round-(.+)", round_cell)
            interview_id = match.group(1) if match else ""
            interviews.append(
                InterviewDetails(
                    id=interview_id,
                    candidate_id=self._current_candidate_id(),
                    job_id=self._current_job_id(),
                    round=cells[0].inner_text().strip(),
                    scheduled_at=cells[1].inner_text().strip(),
                    interviewer=cells[2].inner_text().strip(),
                    status=cells[3].inner_text().strip(),
                )
            )
        return interviews

    def _current_job_id(self) -> str:
        url = self.session.page.url
        match = re.search(r"/jobs/([^/?#]+)", url)
        return match.group(1) if match else ""

    def _current_candidate_id(self) -> str:
        url = self.session.page.url
        match = re.search(r"/candidates/([^/?#]+)", url)
        return match.group(1) if match else ""

    def _job_title_from_breadcrumb(self) -> str:
        breadcrumb = self.session.page.query_selector('a[href^="/jobs/"]')
        return breadcrumb.inner_text().strip() if breadcrumb else ""

    # --- Runner: timing, error conversion, screenshots ---

    def _run(self, action: str, impl) -> ActionResult:
        start = time.monotonic()
        try:
            data = impl()
            duration = (time.monotonic() - start) * 1000
            screenshot = self.session.screenshot(action, success=True)
            return ActionResult(
                action=action, success=True, data=data, screenshot=screenshot, duration_ms=duration
            )
        except _ServerActionError as exc:
            duration = (time.monotonic() - start) * 1000
            screenshot = self.session.screenshot(action, success=False)
            return ActionResult(
                action=action,
                success=False,
                error=errors.ActionError(
                    type=errors.SERVER_ERROR if exc.status_code == "500" else errors.VALIDATION,
                    message=exc.message,
                    page_url=self.session.page.url if self.session.is_launched else None,
                    details={"status_code": exc.status_code},
                ),
                screenshot=screenshot,
                duration_ms=duration,
            )
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            duration = (time.monotonic() - start) * 1000
            screenshot = self.session.screenshot(action, success=False)
            return ActionResult(
                action=action,
                success=False,
                error=errors.from_exception(exc, action, self._safe_page_url()),
                screenshot=screenshot,
                duration_ms=duration,
            )
        except Exception as exc:  # noqa: BLE001 - boundary: convert everything
            duration = (time.monotonic() - start) * 1000
            screenshot = self.session.screenshot(action, success=False)
            return ActionResult(
                action=action,
                success=False,
                error=errors.from_exception(exc, action, self._safe_page_url()),
                screenshot=screenshot,
                duration_ms=duration,
            )

    def _safe_page_url(self) -> str | None:
        try:
            return self.session.page.url if self.session.is_launched else None
        except Exception:  # noqa: BLE001
            return None


class _ServerActionError(Exception):
    """Internal: the app returned an error page instead of completing the action."""

    def __init__(self, message: str, status_code: str) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
