"""Deterministic parser for plain-English interview scheduling goals.

Candidate names are not hard-coded: they are read from the app at run time and
matched against the goal text, so a different candidate, round, interviewer,
date or time works without code changes.

Example: "Schedule a Technical Screen for Aarav Sharma with Priya Nair on 2026-10-08 at 10:00"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
DEFAULT_ROUND = "Interview"
NAME = r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}"


class GoalError(ValueError):
    """The goal is not a schedulable request; carries every problem found."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class InterviewGoal:
    candidate_name: str
    round_name: str
    interviewer: str
    scheduled_at: str  # YYYY-MM-DDTHH:MM, the format of the app's datetime-local field
    assumptions: list[str] = field(default_factory=list)


def _find_names(text: str, names: list[str]) -> list[str]:
    low = text.lower()
    return [n for n in names if re.search(rf"\b{re.escape(n.lower())}\b", low)]


def _parse_date(text: str, today: date) -> str | None:
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    low = text.lower()
    if re.search(r"\btomorrow\b", low):
        return (today + timedelta(days=1)).isoformat()
    if re.search(r"\btoday\b", low):
        return today.isoformat()
    month = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    for pat, d_idx, m_idx in ((rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{month}(?:,?\s+(\d{{4}}))?", 1, 2),
                              (rf"\b{month}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?", 2, 1)):
        if m := re.search(pat, low):
            year = int(m[3]) if m[3] else today.year
            return date(year, MONTHS[m[m_idx][:3]], int(m[d_idx])).isoformat()
    return None


def _parse_time(text: str) -> str | None:
    low = text.lower()
    m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", low) or re.search(
        r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", low) or re.search(r"\b(\d{1,2})()\s*(am|pm)\b", low)
    if not m:
        return None
    hour, minute = int(m[1]), int(m[2] or 0)
    if m[3] == "pm" and hour < 12:
        hour += 12
    if m[3] == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _parse_round(text: str, candidate: str) -> str | None:
    """'Schedule a technical screen for X' -> 'Technical Screen'."""
    m = re.search(rf"\b(?:schedule|book|set up|arrange)\s+(?:an?\s+|the\s+)?(.+?)\s+(?:with\s+{NAME}\s+)?for\s+"
                  rf"{re.escape(candidate)}", text, re.I)
    if not m:
        return None
    words = m[1].strip()
    if words.lower() in ("interview", "an interview", "interviews"):
        return None
    return " ".join(w if w.isupper() else w.capitalize() for w in words.split())


def parse_interview_goal(text: str, candidates: list[str], known_interviewers: list[str] = (),
                         today: date | None = None) -> InterviewGoal:
    today = today or date.today()
    problems: list[str] = []
    if not re.search(r"\b(schedule|book|set up|arrange)\b", text, re.I):
        problems.append("Goal is not an interview scheduling request (expected e.g. 'Schedule a Technical "
                        "Screen for <candidate> with <interviewer> on <date> at <time>').")

    found = _find_names(text, candidates)
    if len(found) != 1:
        problems.append("No known candidate named in the goal." if not found
                        else f"Goal names more than one candidate: {', '.join(found)}.")

    # Interviewer: a known hiring manager if mentioned, else any capitalised name after "with".
    interviewer = next(iter(_find_names(text, list(known_interviewers))), None)
    if interviewer is None and (m := re.search(rf"\bwith\s+({NAME})", text)):
        interviewer = m[1] if m[1] not in found else None
    if not interviewer:
        problems.append("No interviewer found (e.g. 'with Priya Nair').")

    try:
        day = _parse_date(text, today)
    except ValueError:
        day = None
    if day is None:
        problems.append("No valid interview date found (use YYYY-MM-DD, 'October 8' or 'tomorrow').")
    start = _parse_time(text)
    if start is None:
        problems.append("No start time found (e.g. 'at 10:00' or 'at 3pm').")
    if problems:
        raise GoalError(problems)

    assumptions = []
    round_name = _parse_round(text, found[0])
    if round_name is None:
        round_name = DEFAULT_ROUND
        assumptions.append(f"No interview round named; using '{DEFAULT_ROUND}'.")
    return InterviewGoal(found[0], round_name, interviewer, f"{day}T{start}", assumptions)
