"""Recovery decisions the demo app cannot easily produce, using in-memory fakes."""

from datetime import date

import pytest

from app.agent import events as ev
from app.agent.driver import AppUnavailable, CandidatePage, InterviewRow, SubmitResult
from app.agent.goal import GoalError, parse_interview_goal
from app.agent.scheduling import InterviewRequest, schedule_interview

REQ = InterviewRequest("c1", "Test Person", "Technical Screen", "2026-10-08T10:00", "Priya Nair")


class FakeDriver:
    def __init__(self, results, page_readable=lambda n: True, saves_on=()):
        self.results = list(results)  # SubmitResult per attempt
        self.page_readable = page_readable
        self.saves_on = set(saves_on)  # attempt numbers whose request writes the row
        self.rows: list[InterviewRow] = []
        self.submits = 0
        self.reads = 0

    def read_candidate(self, cid):
        self.reads += 1
        if not self.page_readable(self.reads):
            raise AppUnavailable("connection refused")
        return CandidatePage(cid, "Test Person", "shortlisted", list(self.rows))

    def submit_interview(self, cid, round_name, scheduled_at, interviewer, duration_minutes=60):
        self.submits += 1
        res = self.results[self.submits - 1]
        if res.ok or self.submits in self.saves_on:
            self.rows.append(InterviewRow(f"i{self.submits}", round_name, scheduled_at, interviewer, "scheduled"))
        return res


class FakeHarness:
    def __init__(self, driver):
        self.driver = driver

    def source_of_truth(self):
        return {"interviews": [{**r.__dict__, "candidate_id": "c1"} for r in self.driver.rows]}


FAIL_500 = SubmitResult(False, "Interview service unavailable", "server_error", "500")
FAIL_TIMEOUT = SubmitResult(False, "create_interview timed out", "timeout")
FAIL_400 = SubmitResult(False, "Scheduled time must be a valid date/time", "validation", "400")
OK = SubmitResult(True, "accepted")


def go(driver, **kw):
    log = ev.RunLog("unit")
    return schedule_interview(driver, FakeHarness(driver), log, REQ, backoff_seconds=0, **kw), log


def test_unknown_outcome_is_never_retried():
    # Pre-action read works; the post-failure read fails -> we cannot know if it was saved.
    d = FakeDriver([FAIL_TIMEOUT, OK], page_readable=lambda n: n == 1)
    out, log = go(d)
    assert not out.ok and d.submits == 1
    assert "could create a duplicate" in out.blocker
    assert ev.RETRY not in log.types()


def test_permanent_error_is_not_retried():
    d = FakeDriver([FAIL_400, OK])
    out, log = go(d)
    assert not out.ok and d.submits == 1
    assert next(e for e in log.events if e["type"] == ev.RECOVERY_STARTED)["data"]["reason"] == "permanent_error"


def test_timeout_that_saved_is_detected():
    d = FakeDriver([FAIL_TIMEOUT, OK], saves_on={1})
    out, log = go(d)
    assert out.ok and d.submits == 1 and len(d.rows) == 1
    assert ev.RETRY not in log.types()


def test_retries_are_bounded():
    d = FakeDriver([FAIL_500] * 5)
    out, _ = go(d, max_attempts=2)
    assert not out.ok and d.submits == 2 and d.rows == []


def test_verification_catches_a_duplicate():
    d = FakeDriver([OK])
    d.rows.append(InterviewRow("old", "Technical Screen", "2026-10-08T10:00", "Priya Nair", "scheduled"))
    d.page_readable = lambda n: True
    # Pre-check would skip on an exact match, so simulate a duplicate appearing via another client.
    d.read_candidate = lambda cid: CandidatePage(cid, "Test Person", "shortlisted", [])
    out, log = go(d)
    assert not out.ok and "DUPLICATE" in out.blocker


# ---- goal parsing ------------------------------------------------------------

CANDS = ["Aarav Sharma", "Diya Patel", "Ishaan Mehta"]
TODAY = date(2026, 10, 1)


@pytest.mark.parametrize("text, expected", [
    ("Schedule a Technical Screen for Aarav Sharma with Priya Nair on 2026-10-08 at 10:00",
     ("Aarav Sharma", "Technical Screen", "Priya Nair", "2026-10-08T10:00")),
    ("Book a system design round for Ishaan Mehta with Rahul Verma on October 9 at 3pm",
     ("Ishaan Mehta", "System Design Round", "Rahul Verma", "2026-10-09T15:00")),
    ("Please schedule an interview with Ananya Iyer for Diya Patel tomorrow at 9:30 am",
     ("Diya Patel", "Interview", "Ananya Iyer", "2026-10-02T09:30")),
])
def test_parse_variations(text, expected):
    g = parse_interview_goal(text, CANDS, ["Priya Nair", "Rahul Verma", "Ananya Iyer"], today=TODAY)
    assert (g.candidate_name, g.round_name, g.interviewer, g.scheduled_at) == expected


def test_parse_reports_every_missing_field():
    with pytest.raises(GoalError) as err:
        parse_interview_goal("Schedule a call for someone", CANDS, today=TODAY)
    assert len(err.value.problems) == 4  # candidate, interviewer, date, time
