"""Deterministic parser for plain-English interview scheduling goals.

Names are not hard-coded: the candidate and interviewer lists are read from the
app at run time and matched against the goal text, so a different candidate,
interviewer, date, time or duration works without code changes.

Example: "Schedule a 45-minute interview for Rahul Sharma with Vikram Desai on 2026-10-02 at 10:00"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
DEFAULT_DURATION = 60


class GoalError(ValueError):
    """The goal is not a schedulable request; carries every problem found."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class InterviewGoal:
    candidate_name: str
    interviewer: str
    date: str  # YYYY-MM-DD
    start_time: str  # HH:MM
    duration_minutes: int
    assumptions: list[str] = field(default_factory=list)


def _find_name(text: str, names: list[str]) -> list[str]:
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


def _parse_duration(text: str) -> int | None:
    low = text.lower()
    if m := re.search(r"\b(\d{1,3})\s*-?\s*(?:min|mins|minute|minutes)\b", low):
        return int(m[1])
    if re.search(r"\b(?:an|one|1)\s*-?\s*hours?\b", low):
        return 60
    if re.search(r"\bhalf\s*-?\s*(?:an\s+)?hour\b", low):
        return 30
    return None


def parse_interview_goal(text: str, candidates: list[str], interviewers: list[str],
                         today: date | None = None) -> InterviewGoal:
    today = today or date.today()
    problems: list[str] = []
    if not re.search(r"\b(schedule|book|set up|arrange)\b", text, re.I) or "interview" not in text.lower():
        problems.append("Goal is not an interview scheduling request "
                        "(expected e.g. 'Schedule an interview for <candidate> with <interviewer> on <date> at <time>').")

    # Interviewer names are matched first so they are not mistaken for candidates.
    who = _find_name(text, interviewers)
    cands = [c for c in _find_name(text, candidates) if c not in who]
    if len(cands) != 1:
        problems.append("No known candidate named in the goal." if not cands
                        else f"Goal names more than one candidate: {', '.join(cands)}.")
    if len(who) != 1:
        problems.append(f"Name exactly one interviewer ({', '.join(interviewers)})." if not who
                        else f"Goal names more than one interviewer: {', '.join(who)}.")
    try:
        day = _parse_date(text, today)
    except ValueError:
        day = None
    if day is None:
        problems.append("No valid interview date found (use YYYY-MM-DD, 'October 2' or 'tomorrow').")
    start = _parse_time(text)
    if start is None:
        problems.append("No start time found (e.g. 'at 10:00' or 'at 3pm').")
    if problems:
        raise GoalError(problems)

    assumptions = []
    duration = _parse_duration(text)
    if duration is None:
        duration = DEFAULT_DURATION
        assumptions.append(f"No duration given; using the app default of {DEFAULT_DURATION} minutes.")
    return InterviewGoal(cands[0], who[0], day, start, duration, assumptions)
