"""Action ledger: one record per important action the agent takes.

The event log (events.py) is a narrative stream. The ledger is the auditable
record behind it: for every action, what was asked (input), what came back
(result / error), whether it was a recovery step for an earlier failure, and
the screenshots taken while doing it. The verification layer and the final
report are built from the ledger plus the app's own records.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

SUCCEEDED, FAILED, SKIPPED = "succeeded", "failed", "skipped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class ActionRecord:
    id: str
    action: str  # machine name, e.g. create_interview
    title: str  # what a person reads, e.g. "Schedule Technical Screen for Aarav Sharma"
    input: dict
    target: str | None = None  # candidate id the action is about, if any
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    status: str = "running"  # running | succeeded | failed | skipped
    result: dict | None = None
    error: str | None = None
    # Set when this action is part of recovering from an earlier failed action:
    # {"recovers": <record id>, "decision": ..., "reason": ...}
    recovery: dict | None = None
    evidence: list[str] = field(default_factory=list)  # screenshot paths


class ActionLedger:
    def __init__(self):
        self._records: list[ActionRecord] = []
        self._lock = threading.Lock()

    def begin(self, action: str, title: str, input: dict | None = None, *, target: str | None = None,
              recovery: dict | None = None) -> ActionRecord:
        with self._lock:
            rec = ActionRecord(f"A{len(self._records) + 1}", action, title, input or {},
                               target=target, recovery=recovery)
            self._records.append(rec)
            return rec

    def finish(self, rec: ActionRecord, status: str, *, result: dict | None = None, error: str | None = None,
               evidence: list[str | None] | str | None = None, title: str | None = None) -> ActionRecord:
        with self._lock:
            rec.status, rec.result, rec.error = status, result, error
            rec.finished_at = _now()
            if title:
                rec.title = title
            shots = [evidence] if isinstance(evidence, str) else (evidence or [])
            rec.evidence += [s for s in shots if s]
            return rec

    def add_recovery(self, rec: ActionRecord, recovers: str, decision: str, reason: str) -> None:
        with self._lock:
            rec.recovery = {"recovers": recovers, "decision": decision, "reason": reason}

    @property
    def records(self) -> list[ActionRecord]:
        with self._lock:
            return list(self._records)

    def to_list(self) -> list[dict]:
        return [asdict(r) for r in self.records]
