"""OpsPilot browser automation layer (Phase 3).

A reliable Playwright-based tool interface for operating the synthetic
recruitment application. Every action returns a structured ActionResult with
typed data or a structured error — no business logic lives here.

Quick start:
    from automation import RecruitmentBrowser

    with RecruitmentBrowser(base_url="http://127.0.0.1:5000") as browser:
        result = browser.open_jobs()
        if result.success:
            for job in result.data:
                print(job.title)
"""

from .actions import RecruitmentBrowser
from .results import (
    ActionError,
    ActionResult,
    CandidateDetails,
    CandidateSummary,
    InterviewDetails,
    InterviewVerification,
    InvitationDetails,
    JobSummary,
    StatusUpdate,
)

__all__ = [
    "ActionError",
    "ActionResult",
    "CandidateDetails",
    "CandidateSummary",
    "InterviewDetails",
    "InterviewVerification",
    "InvitationDetails",
    "JobSummary",
    "RecruitmentBrowser",
    "StatusUpdate",
]
