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
DEFAULT_DURATION = 60
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DURATION = re.compile(r"\b(\d{2,3})\s*-?\s*(?:min|mins|minute|minutes)\b|\b(an|one|1|2|two)\s*-?\s*hours?\b", re.I)
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
    send_invitation: bool = True  # always asks for approval first; False if the goal opts out
    assumptions: list[str] = field(default_factory=list)
    duration_minutes: int = DEFAULT_DURATION


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
    if m := re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", low):
        ahead = (WEEKDAYS.index(m[1]) - today.weekday()) % 7 or 7  # the next one, never today
        return (today + timedelta(days=ahead)).isoformat()
    month = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    for pat, d_idx, m_idx in ((rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{month}(?:,?\s+(\d{{4}}))?", 1, 2),
                              (rf"\b{month}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?", 2, 1)):
        if m := re.search(pat, low):
            year = int(m[3]) if m[3] else today.year
            return date(year, MONTHS[m[m_idx][:3]], int(m[d_idx])).isoformat()
    return None


def _avoid_weekend(day: str, text: str, today: date, assumptions: list[str]) -> str:
    """'today' / 'tomorrow' that land on a weekend move to the next business day (stated in the plan).
    An explicit date or weekday name is taken as written."""
    d = date.fromisoformat(day)
    if d.weekday() < 5 or not re.search(r"\b(today|tomorrow)\b", text, re.I):
        return day
    moved = _next_business_day(d - timedelta(days=1))
    assumptions.append(f"{d:%A} {day} is a weekend day; using the next business day, {moved:%A} {moved.isoformat()}.")
    return moved.isoformat()


def _next_business_day(today: date) -> date:
    d = today + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


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
    words = _DURATION.sub("", m[1]).strip()
    if words.lower() in ("", "interview", "an interview", "interviews"):
        return None
    return " ".join(w if w.isupper() else w.capitalize() for w in words.split())


def _parse_duration(text: str) -> int | None:
    m = _DURATION.search(text)
    if not m:
        return None
    if m[1]:
        return int(m[1])
    return 120 if m[2].lower() in ("2", "two") else 60


_NO_INVITE = re.compile(r"\b(?:do not|don't|dont|without|skip|no)\s+(?:sending\s+|send\s+)?(?:an?\s+|the\s+|any\s+)?"
                        r"(?:invit\w+|e-?mail\w*)", re.I)


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
    day = _avoid_weekend(day, text, today, assumptions)
    round_name = _parse_round(text, found[0])
    if round_name is None:
        round_name = DEFAULT_ROUND
        assumptions.append(f"No interview round named; using '{DEFAULT_ROUND}'.")
    send = not _NO_INVITE.search(text)
    if send:
        assumptions.append("Sending the invitation is part of this goal; it needs your approval first.")
    duration = _parse_duration(text)
    if duration is None:
        duration = DEFAULT_DURATION
        assumptions.append(f"No duration given; using {DEFAULT_DURATION} minutes.")
    return InterviewGoal(found[0], round_name, interviewer, f"{day}T{start}", send, assumptions, duration)


# ---- batch goals ---------------------------------------------------------------

PIPELINE_STATUSES = ["applied", "screening", "shortlisted", "interview", "offer"]
# Parts of the day a goal can name, as (first possible start, latest end).
DAY_PARTS = {"morning": ("09:00", "12:00"), "afternoon": ("13:00", "17:00"), "evening": ("17:00", "19:00")}
WORKDAY = ("10:00", "18:00")


@dataclass
class BatchInterviewGoal:
    """'Schedule interviews for all shortlisted AI Engineer candidates tomorrow afternoon' and variations."""

    job_title: str
    candidate_status: str
    round_name: str
    interviewer: str | None  # None -> the job's hiring manager
    date: str  # YYYY-MM-DD
    window_start: str  # HH:MM, earliest slot start
    window_end: str  # HH:MM, every interview must end by this time
    duration_minutes: int
    send_invitation: bool = True
    assumptions: list[str] = field(default_factory=list)


def _parse_batch_round(text: str, status: str) -> str | None:
    m = re.search(rf"\b(?:schedule|book|set up|arrange)\s+(?:an?\s+|the\s+)?(.+?)\s+for\s+(?:all\s+)?(?:the\s+|our\s+)?"
                  rf"{status}\b", text, re.I)
    if not m:
        return None
    words = _DURATION.sub("", m[1]).split()
    if not words or " ".join(words).lower() in ("interview", "interviews", "an interview"):
        return None
    if words[-1].lower().endswith("s") and not words[-1].lower().endswith("ss"):
        words[-1] = words[-1][:-1]  # "technical screens" -> "technical screen"
    return " ".join(w if w.isupper() else w.capitalize() for w in words)


def parse_batch_goal(text: str, job_titles: list[str], today: date | None = None) -> BatchInterviewGoal | None:
    """Return a batch goal if the text targets a group of candidates ('... shortlisted X candidates'), else None."""
    today = today or date.today()
    low = text.lower()
    if not re.search(r"\b(schedule|book|set up|arrange)\b", low):
        return None
    status = next((s for s in PIPELINE_STATUSES
                   if re.search(rf"\b{s}\b[\w\s-]{{0,40}}?\bcandidates\b", low)), None)
    if status is None:
        return None
    jobs = _find_names(text, job_titles)
    if len(jobs) != 1:
        raise GoalError([f"Name exactly one job ({', '.join(job_titles)})." if not jobs
                         else f"Goal names more than one job: {', '.join(jobs)}."])

    assumptions: list[str] = []
    round_name = _parse_batch_round(text, status)
    if round_name is None:
        round_name = DEFAULT_ROUND
        assumptions.append(f"No interview round named; using '{DEFAULT_ROUND}'.")
    m = re.search(rf"\bwith\s+({NAME})", text)
    interviewer = m[1] if m and m[1] not in jobs else None
    if interviewer is None:
        assumptions.append(f"No interviewer named; using the {jobs[0]} hiring manager.")
    try:
        day = _parse_date(text, today)
    except ValueError:
        raise GoalError(["The interview date in the goal is not a valid date."]) from None
    if day is None:
        day = _next_business_day(today).isoformat()
        assumptions.append(f"No date given; using the next business day, {day}.")
    day = _avoid_weekend(day, text, today, assumptions)
    if date.fromisoformat(day) < today:
        raise GoalError([f"The interview date {day} is in the past."])

    part = next((p for p in DAY_PARTS if re.search(rf"\b{p}\b", low)), None)
    start = _parse_time(text)
    if start is not None:
        window = (start, DAY_PARTS[part][1] if part else WORKDAY[1])
    elif part:
        window = DAY_PARTS[part]
    else:
        window = WORKDAY
        assumptions.append(f"No time given; using working hours {window[0]}-{window[1]}.")
    duration = _parse_duration(text)
    if duration is None:
        duration = DEFAULT_DURATION
        assumptions.append(f"No duration given; using {DEFAULT_DURATION} minutes.")
    send = not _NO_INVITE.search(text)
    if send:
        assumptions.append("Sending each invitation is part of this goal; each one needs your approval first.")
    return BatchInterviewGoal(jobs[0], status, round_name, interviewer, day, window[0], window[1], duration,
                              send, assumptions)
