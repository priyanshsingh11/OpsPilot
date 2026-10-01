"""Independent verification of a run's outcome.

Kept apart from the agent: it does not look at what the executor says it did. It
compares the app's records before the run with the records after it (both read
from /api/state) against what the goal asked for, and decides the final status:

    COMPLETED            every target is fully done and every check passes
    PARTIALLY_COMPLETED  some targets are done (or all are, but a safety check failed)
    FAILED               nothing the goal asked for was achieved

Each unfinished target gets a plain-language entry saying exactly what remains and why.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

COMPLETED, PARTIAL, FAILED = "completed", "partially_completed", "failed"


@dataclass
class Expectation:
    """What the goal requires for one candidate."""

    candidate_id: str
    candidate_name: str
    target_status: str
    round_name: str
    scheduled_at: str | None  # None when no slot could be planned
    interviewer: str | None
    duration_minutes: int
    expect_invitation: bool
    reason_if_missing: str | None = None  # the executor's blocker, used only to explain


@dataclass
class Check:
    key: str
    name: str
    passed: bool
    detail: str


@dataclass
class TargetResult:
    candidate_id: str
    candidate_name: str
    done: bool
    interview_id: str | None
    missing: list[str] = field(default_factory=list)
    reason: str | None = None


@dataclass
class Report:
    outcome: str
    headline: str
    checks: list[Check]
    targets: list[TargetResult]
    remaining: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _dt(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _matches(iv: dict, e: Expectation) -> bool:
    return (iv["candidate_id"] == e.candidate_id and iv["status"] == "scheduled"
            and iv["round"].strip().lower() == e.round_name.strip().lower()
            and e.scheduled_at is not None and _dt(iv["scheduled_at"]) == _dt(e.scheduled_at)
            and (e.interviewer or "").strip().lower() == iv["interviewer"].strip().lower()
            and int(iv.get("duration_minutes") or 60) == e.duration_minutes)


def verify(expectations: list[Expectation], before: dict, after: dict) -> Report:
    cands_before = {c["id"]: c for c in before["candidates"]}
    cands_after = {c["id"]: c for c in after["candidates"]}
    targets = {e.candidate_id for e in expectations}
    interviews, invitations = after["interviews"], after.get("invitations", [])

    results: list[TargetResult] = []
    for e in expectations:
        missing = []
        same_round = [iv for iv in interviews if iv["candidate_id"] == e.candidate_id
                      and iv["status"] == "scheduled" and iv["round"].strip().lower() == e.round_name.lower()]
        match = [iv for iv in same_round if _matches(iv, e)]
        interview = match[0] if len(match) == 1 and len(same_round) == 1 else None
        if interview is None:
            missing.append(f"no '{e.round_name}' interview record" if not match
                           else f"{len(match)} matching '{e.round_name}' interviews (expected exactly 1)")
        status = cands_after.get(e.candidate_id, {}).get("status")
        if status != e.target_status:
            missing.append(f"status is '{status}', not '{e.target_status}'")
        if e.expect_invitation:
            sent = [v for v in invitations if interview and v["interview_id"] == interview["id"]
                    and v["status"] == "sent"]
            if len(sent) != 1:
                missing.append("invitation not sent" if not sent else f"{len(sent)} invitations sent (expected 1)")
        results.append(TargetResult(e.candidate_id, e.candidate_name, not missing,
                                    interview["id"] if interview else None, missing,
                                    None if not missing else e.reason_if_missing))

    n = len(expectations)
    checks: list[Check] = []

    def count(pred) -> int:
        return sum(1 for r in results if pred(r))

    k = count(lambda r: r.interview_id is not None)
    checks.append(Check("interviews_exist", "Interview records exist", k == n,
                        f"{k} of {n} expected interview records exist in the app"))
    k = count(lambda r: not any(m.startswith("status") for m in r.missing))
    checks.append(Check("statuses", "Candidate statuses updated", k == n,
                        f"{k} of {n} candidates have the expected status"))
    invited = [e for e in expectations if e.expect_invitation]
    if invited:
        ids = {e.candidate_id for e in invited}
        k = count(lambda r: r.candidate_id in ids and not any("invitation" in m for m in r.missing))
        checks.append(Check("invitations", "Invitations sent", k == len(invited),
                            f"{k} of {len(invited)} approved invitations recorded as sent"))

    dupes = []
    for cid in targets:
        rounds: dict[str, int] = {}
        for iv in interviews:
            if iv["candidate_id"] == cid and iv["status"] == "scheduled":
                rounds[iv["round"].lower()] = rounds.get(iv["round"].lower(), 0) + 1
        dupes += [f"{cid}: {c}x '{r}'" for r, c in rounds.items() if c > 1]
        per_interview: dict[str, int] = {}
        for v in invitations:
            if v["candidate_id"] == cid:
                per_interview[v["interview_id"]] = per_interview.get(v["interview_id"], 0) + 1
        dupes += [f"{cid}: {c} invitations for {i}" for i, c in per_interview.items() if c > 1]
    checks.append(Check("no_duplicates", "No duplicate interviews or invitations", not dupes,
                        "No duplicates found" if not dupes else "Duplicates: " + "; ".join(dupes)))

    bad_fields = []
    for r in results:
        iv = next((i for i in interviews if i["id"] == r.interview_id), None)
        if iv is None:
            continue
        if not (iv["round"].strip() and iv["interviewer"].strip() and _dt(iv["scheduled_at"])
                and int(iv.get("duration_minutes") or 0) > 0
                and iv["job_id"] == cands_after[r.candidate_id]["job_id"]):
            bad_fields.append(iv["id"])
    checks.append(Check("required_fields", "All required fields present", not bad_fields,
                        "Round, time, duration, interviewer and job are set on every new interview"
                        if not bad_fields else f"Incomplete records: {', '.join(bad_fields)}"))

    clashes = []
    scheduled = [i for i in interviews if i["status"] == "scheduled" and _dt(i["scheduled_at"])]
    mine = [i for i in scheduled if i["id"] in {r.interview_id for r in results}]
    for a in mine:
        a0 = _dt(a["scheduled_at"]); a1 = a0 + timedelta(minutes=int(a.get("duration_minutes") or 60))
        for b in scheduled:
            if b["id"] == a["id"] or b["interviewer"].lower() != a["interviewer"].lower():
                continue
            b0 = _dt(b["scheduled_at"]); b1 = b0 + timedelta(minutes=int(b.get("duration_minutes") or 60))
            if a0 < b1 and b0 < a1:
                clashes.append(f"{a['id']} overlaps {b['id']} ({a['interviewer']})")
    checks.append(Check("no_double_booking", "No interviewer double-booked", not clashes,
                        "No overlapping interviews for the interviewers involved" if not clashes
                        else "; ".join(sorted(set(clashes)))))

    unintended = []
    for cid, c in cands_before.items():
        if cid not in targets and cands_after.get(cid, {}).get("status") != c["status"]:
            unintended.append(f"{c['name']}'s status changed")
    old_ids = {i["id"] for i in before["interviews"]}
    for iv in interviews:
        if iv["id"] not in old_ids and iv["candidate_id"] not in targets:
            unintended.append(f"new interview {iv['id']} for a candidate outside the goal")
    old_by_id = {i["id"]: i for i in before["interviews"]}
    for iv in interviews:
        prev = old_by_id.get(iv["id"])
        if prev and (prev["status"], prev["scheduled_at"]) != (iv["status"], iv["scheduled_at"]):
            unintended.append(f"existing interview {iv['id']} was modified")
    old_inv = {v["id"] for v in before.get("invitations", [])}
    for v in invitations:
        if v["id"] not in old_inv and v["candidate_id"] not in targets:
            unintended.append(f"invitation {v['id']} sent to a candidate outside the goal")
    checks.append(Check("no_unintended_changes", "No unintended changes", not unintended,
                        "Nothing outside the goal was changed" if not unintended else "; ".join(unintended)))

    done = [r for r in results if r.done]
    all_checks = all(c.passed for c in checks)
    if n == 0:
        outcome = COMPLETED if all_checks else PARTIAL
        headline = "Nothing to do: no candidates matched the goal." if all_checks else "Unexpected changes found."
    elif len(done) == n and all_checks:
        outcome, headline = COMPLETED, f"All {n} candidate(s) done and verified."
    elif done:
        outcome = PARTIAL
        headline = f"{len(done)} of {n} candidate(s) done and verified; {n - len(done)} not finished."
        if len(done) == n:
            headline = f"All {n} candidate(s) done, but a safety check failed."
    elif scheduled_some := [r for r in results if r.interview_id]:
        # Nothing is fully done, but real work happened (e.g. the user rejected every invitation).
        outcome = PARTIAL
        headline = (f"0 of {n} candidate(s) fully done; interviews are scheduled for {len(scheduled_some)} "
                    "but the rest of the goal is not finished.")
    else:
        outcome, headline = FAILED, f"None of the {n} candidate(s) could be completed."

    remaining = [f"{r.candidate_name}: " + "; ".join(r.missing) + (f". Why: {r.reason}" if r.reason else "")
                 for r in results if not r.done]
    remaining += [f"Fix: {c.name.lower()} ({c.detail})" for c in checks
                  if not c.passed and c.key in ("no_duplicates", "no_unintended_changes",
                                                "no_double_booking", "required_fields")]
    return Report(outcome, headline, checks, results, remaining)
