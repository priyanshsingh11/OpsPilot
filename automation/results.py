"""Structured results returned by every browser action.

Every action returns an ActionResult so a future agent (or test) can decide
what to do next without parsing strings: success/failure is explicit, domain
data is typed, and failures carry enough structured context to reason about.
"""

from dataclasses import dataclass, field
from typing import Any, Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass
class ActionError:
    """Structured failure information exposed by browser actions.

    Attributes:
        type: Machine-readable failure category — one of "timeout",
            "not_found", "server_error", "validation", "navigation",
            "browser", "unknown".
        message: Human-readable description of what failed.
        page_url: URL of the page when the failure was detected, if any.
        details: Extra structured context (e.g. HTTP status, selector that
            could not be resolved, server error text).
    """

    type: str
    message: str
    page_url: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult(Generic[T]):
    """Result of a single browser action.

    Attributes:
        action: Name of the action that produced this result.
        success: Whether the action completed its goal.
        data: Typed domain data on success (e.g. list of candidates).
        error: Structured failure information on failure.
        screenshot: Path to a screenshot captured around the action, if any.
        duration_ms: Wall-clock time the action took.
    """

    action: str
    success: bool
    data: Optional[T] = None
    error: Optional[ActionError] = None
    screenshot: Optional[str] = None
    duration_ms: float = 0.0


# --- Domain data types (plain data, no behaviour) ---


@dataclass
class JobSummary:
    id: str
    title: str
    department: str
    location: str
    status: str
    hiring_manager: str
    candidate_count: int


@dataclass
class CandidateSummary:
    id: str
    name: str
    email: str
    job_id: str
    status: str
    experience_years: int
    skills: list[str]


@dataclass
class CandidateDetails:
    id: str
    name: str
    email: str
    job_id: str
    job_title: str
    status: str
    experience_years: int
    skills: list[str]
    summary: str


@dataclass
class InterviewDetails:
    id: str
    candidate_id: str
    job_id: str
    round: str
    scheduled_at: str
    interviewer: str
    status: str


@dataclass
class StatusUpdate:
    candidate_id: str
    previous_status: str
    new_status: str


@dataclass
class InterviewVerification:
    """Outcome of checking that an interview exists for a candidate."""

    candidate_id: str
    round_name: str
    found: bool
    interview: Optional[InterviewDetails] = None
    scheduled_interviews: list[InterviewDetails] = field(default_factory=list)
