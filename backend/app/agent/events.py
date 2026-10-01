"""Structured run log.

Every agent decision is recorded as an event with a fixed type, a human-readable
message and machine-readable data. The same events feed the dashboard timeline,
the stored run record, and the process log (one JSON line per event).
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger("opspilot.agent")

# Event types. The recovery path is ACTION_FAILED -> STATE_CHECK -> RECOVERY_STARTED
# -> RETRY -> VERIFICATION -> COMPLETED (or BLOCKED).
GOAL_RECEIVED = "GOAL_RECEIVED"
PLAN = "PLAN"
SETUP = "SETUP"
STATE_CHECK = "STATE_CHECK"
ACTION = "ACTION"
ACTION_SUCCEEDED = "ACTION_SUCCEEDED"
ACTION_FAILED = "ACTION_FAILED"
RECOVERY_STARTED = "RECOVERY_STARTED"
RETRY = "RETRY"
SKIPPED = "SKIPPED"
VERIFICATION = "VERIFICATION"
COMPLETED = "COMPLETED"
BLOCKED = "BLOCKED"
# Human control.
APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
APPROVAL_GRANTED = "APPROVAL_GRANTED"
PAUSED = "PAUSED"
RESUMED = "RESUMED"  # the user continued a paused run
RUN_RESTARTED = "RUN_RESTARTED"  # a run interrupted by a restart was picked up again
REJECTED = "REJECTED"  # the user declined the approval; the run ends here
STOPPED = "STOPPED"

TERMINAL = {COMPLETED, BLOCKED, REJECTED, STOPPED}


class RunLog:
    """Append-only, thread-safe event log for one agent run."""

    def __init__(self, run_id: str, pace_seconds: float = 0.0, events: list[dict] | None = None,
                 on_emit: Callable[[list[dict]], None] | None = None):
        self.run_id = run_id
        # Called after every event with the full list, so the run is persisted as it happens.
        self.on_emit = on_emit
        # Pause after each non-terminal event so a live run can be followed on screen.
        self.pace_seconds = pace_seconds
        self._events: list[dict] = list(events or [])  # a resumed run continues its log
        self._lock = threading.Lock()

    def emit(self, type_: str, message: str, **data) -> dict:
        with self._lock:
            event = {
                "seq": len(self._events) + 1,
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "type": type_,
                "message": message,
                "data": data,
            }
            self._events.append(event)
        logger.info(
            "%s", json.dumps({"run": self.run_id, **event}, default=str),
            extra={"event_type": type_},
        )
        if self.on_emit is not None:
            try:
                self.on_emit(self.events)
            except Exception:  # persistence trouble must not kill the run
                logger.exception("could not persist run events")
        if self.pace_seconds and type_ not in TERMINAL:
            time.sleep(self.pace_seconds)
        return event

    @property
    def events(self) -> list[dict]:
        with self._lock:
            return list(self._events)

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]
