"""Human control over a run: pause, stop, and approval for high-impact actions.

The agent calls `checkpoint()` between steps; that is where a pause or stop takes
effect (an action already in the browser is allowed to finish, so nothing is left
half-done). `request_approval()` blocks until the user decides.

The approval cannot be bypassed from agent code: the only way to send an invitation
is `BrowserDriver.send_invitation(grant, ...)`, and a grant is only minted here after
the decision is read back from the database as "approved". The driver re-checks the
database at the moment of the action, so a stop or a changed payload invalidates it.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Callable

from . import events as ev
from .store import RunStore


class RunStopped(Exception):
    """The user stopped the run; unwinds to the runner, which reports what was done."""


class ApprovalRequired(Exception):
    """A high-impact action was attempted without a valid, current approval."""


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class ApprovalGrant:
    """Proof that the user approved exactly this payload. Only RunControl creates these."""

    approval_id: str
    action: str
    payload_hash: str
    _still_approved: Callable[[], bool] = field(repr=False, compare=False)

    def authorizes(self, action: str, payload: dict) -> bool:
        return (action == self.action and payload_hash(payload) == self.payload_hash
                and self._still_approved())


class RunControl:
    def __init__(self, run_id: str, store: RunStore, log: ev.RunLog, poll_seconds: float = 0.2):
        self.run_id, self.store, self.log, self.poll = run_id, store, log, poll_seconds
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._cond = threading.Condition()
        self.checkpoint_state: dict = (store.get_run(run_id) or {}).get("checkpoint") or {}

    # ---- called from API threads
    @property
    def pause_requested(self) -> bool:
        return self._pause.is_set()

    def request_pause(self) -> None:
        self._pause.set()

    def request_continue(self) -> None:
        self._pause.clear()
        self._wake()

    def request_stop(self) -> None:
        self._stop.set()
        self._wake()

    def _wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    # ---- called from the agent thread
    def save(self, **updates) -> None:
        """Merge progress into the persisted checkpoint (steps are merged one level deep)."""
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(self.checkpoint_state.get(key), dict):
                self.checkpoint_state[key] = {**self.checkpoint_state[key], **value}
            else:
                self.checkpoint_state[key] = value
        self.store.save_checkpoint(self.run_id, self.checkpoint_state)

    def checkpoint(self) -> None:
        """Between steps: stop if asked, hold here while paused."""
        if self._stop.is_set():
            raise RunStopped()
        if not self._pause.is_set():
            return
        self.store.set_status(self.run_id, "paused")
        self.log.emit(ev.PAUSED, "Paused by the user. No further actions will run until you continue.")
        with self._cond:
            while self._pause.is_set() and not self._stop.is_set():
                self._cond.wait(self.poll)
        if self._stop.is_set():
            raise RunStopped()
        self.store.set_status(self.run_id, "running")
        self.log.emit(ev.RESUMED, "Continued by the user.")

    def request_approval(self, action: str, payload: dict, details: dict) -> ApprovalGrant | None:
        """Block until the user approves (returns a grant) or rejects (returns None)."""
        digest = payload_hash(payload)
        approval = self.store.find_approval(self.run_id, action, digest)
        if approval is None:
            self.store.cancel_pending(self.run_id)  # an older request for a different payload is void
            approval = self.store.create_approval(self.run_id, action, payload, digest, details)
            self.log.emit(ev.APPROVAL_REQUESTED, details.get("title", action) + ": waiting for your approval.",
                          approval_id=approval["id"], action=action)
        else:
            self.log.emit(ev.STATE_CHECK, f"Found an earlier approval request ({approval['id']}) for this "
                          f"exact action: status '{approval['status']}'.", approval_id=approval["id"])
        approval_id = approval["id"]
        self.save(approval_id=approval_id)

        if approval["status"] == "pending":
            self.store.set_status(self.run_id, "awaiting_approval")
            with self._cond:
                while True:
                    if self._stop.is_set():
                        raise RunStopped()
                    current = self.store.get_approval(approval_id)
                    if current["status"] != "pending":
                        approval = current
                        break
                    self._cond.wait(self.poll)
            self.store.set_status(self.run_id, "running")

        if approval["status"] == "approved":
            self.log.emit(ev.APPROVAL_GRANTED, "Approved by the user. Continuing.", approval_id=approval_id)
            return ApprovalGrant(approval_id, action, digest,
                                 lambda: (self.store.get_approval(approval_id) or {}).get("status") == "approved")
        if approval["status"] == "rejected":
            return None
        raise RunStopped()  # cancelled: the run was stopped
