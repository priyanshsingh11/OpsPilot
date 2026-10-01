"""Starts agent runs in the background, serves their live state, and carries the user's controls.

One run at a time: two runs operating the same app concurrently would make each
other's state checks meaningless. Everything about a run is persisted as it happens (events,
checkpoint, approvals), so a run that dies with the process shows up as "interrupted" and can
be resumed: the agent re-checks the app's state at every step, so resuming never repeats work.
"""

from __future__ import annotations

import threading
import uuid
from typing import Callable

from ..config import settings
from ..database import SessionLocal
from .control import RunControl
from .driver import BrowserDriver, HarnessClient
from .events import RunLog
from .events import STOPPED
from .runner import AgentOptions, RunResult, run_goal
from .store import ACTIVE_STATUSES, RunStore


class RunInProgress(Exception):
    pass


class ControlError(Exception):
    """The requested control does not apply to the run's current state (HTTP 409)."""


class NotFound(Exception):
    pass


def _default_tools():
    harness = HarnessClient.connect(settings.demo_app_url, settings.demo_app_timeout_seconds)
    driver = BrowserDriver.launch(settings.demo_app_url, headless=settings.agent_headless)
    return driver, harness


class RunManager:
    def __init__(self, tools_factory: Callable = _default_tools, options: AgentOptions | None = None,
                 session_factory=SessionLocal):
        self.tools_factory = tools_factory
        self.options = options or AgentOptions(
            max_attempts=settings.agent_max_attempts,
            retry_backoff_seconds=settings.agent_retry_backoff_seconds,
            step_delay_seconds=settings.agent_step_delay_seconds,
        )
        self.store = RunStore(session_factory)
        self._controls: dict[str, RunControl] = {}
        self._lock = threading.Lock()
        self._active: str | None = None

    def recover(self) -> int:
        """At startup: runs left active by a previous process lost their thread. Make them resumable."""
        return self.store.mark_interrupted()

    # ---- starting and resuming
    def start(self, goal: str, scenario: str | None, reset_demo_data: bool) -> str:
        with self._lock:
            if self._active:
                raise RunInProgress(self._active)
            run_id = uuid.uuid4().hex[:12]
            self._active = run_id
        self.store.create_run(run_id, goal, scenario)
        self._launch(run_id, goal, scenario, reset_demo_data, [])
        return run_id

    def resume(self, run_id: str) -> None:
        run = self._require_run(run_id)
        if run["status"] != "interrupted":
            raise ControlError(f"Only an interrupted run can be resumed (this one is '{run['status']}').")
        with self._lock:
            if self._active:
                raise RunInProgress(self._active)
            self._active = run_id
        self.store.reopen_run(run_id)
        # No scenario / data reset on resume: that would change the world under the run.
        self._launch(run_id, run["goal"], None, False, run["events"])

    def _launch(self, run_id, goal, scenario, reset, events) -> None:
        log = RunLog(run_id, events=events, on_emit=lambda evs: self.store.save_events(run_id, evs))
        self._controls[run_id] = RunControl(run_id, self.store, log)
        threading.Thread(target=self._execute, args=(run_id, goal, scenario, reset, log),
                         name=f"agent-run-{run_id}", daemon=True).start()

    def _execute(self, run_id: str, goal: str, scenario: str | None, reset: bool, log: RunLog) -> None:
        control = self._controls[run_id]
        driver = harness = None
        try:
            # Playwright's sync API is bound to the thread that launched it, so launch here.
            driver, harness = self.tools_factory()
            result = run_goal(goal, driver, harness, log, scenario=scenario, reset_demo_data=reset,
                              options=self.options, control=control)
        except Exception as exc:  # e.g. browser failed to launch
            log.emit("BLOCKED", f"Agent could not start: {exc.__class__.__name__}: {exc}")
            result = RunResult("blocked", "Goal not completed", blocker=str(exc))
        finally:
            for tool in (driver, harness):
                if tool is not None:
                    try:
                        tool.close()
                    except Exception:
                        pass
        self.store.cancel_pending(run_id)  # no approval stays open once the run has ended
        self.store.finish_run(run_id, result.status, result.summary, result.blocker, result.details, log.events)
        with self._lock:
            self._active = None
            self._controls.pop(run_id, None)

    # ---- user controls
    def _require_run(self, run_id: str) -> dict:
        run = self.store.get_run(run_id)
        if run is None:
            raise NotFound("Run not found")
        return run

    def _live_control(self, run_id: str) -> RunControl:
        self._require_run(run_id)
        control = self._controls.get(run_id)
        if control is None:
            raise ControlError("This run is not executing right now.")
        return control

    def pause(self, run_id: str) -> None:
        control = self._live_control(run_id)
        if self.store.get_run(run_id)["status"] != "running":
            raise ControlError("Only a running run can be paused.")
        control.request_pause()

    def continue_run(self, run_id: str) -> None:
        control = self._live_control(run_id)
        if not control.pause_requested:
            raise ControlError("The run is not paused.")
        control.request_continue()

    def stop(self, run_id: str) -> None:
        run = self._require_run(run_id)
        control = self._controls.get(run_id)
        if control is not None:
            self.store.cancel_pending(run_id)
            control.request_stop()
        elif run["status"] == "interrupted":
            # No thread to unwind: record the stop directly.
            self.store.cancel_pending(run_id)
            log = RunLog(run_id, events=run["events"], on_emit=lambda evs: self.store.save_events(run_id, evs))
            msg = "Stopped by the user while the run was interrupted. No further actions will be taken."
            log.emit(STOPPED, msg)
            self.store.finish_run(run_id, "stopped", msg, None, run["details"], log.events)
        else:
            raise ControlError(f"A run that is '{run['status']}' cannot be stopped.")

    def decide(self, run_id: str, approval_id: str, decision: str) -> None:
        self._require_run(run_id)
        approval = self.store.get_approval(approval_id)
        if approval is None or approval["run_id"] != run_id:
            raise NotFound("Approval not found for this run")
        if not self.store.decide(approval_id, decision):
            raise ControlError(f"This approval was already {approval['status']}.")
        control = self._controls.get(run_id)
        if control is not None:
            control._wake()

    # ---- reading
    def get(self, run_id: str) -> dict | None:
        run = self.store.get_run(run_id)
        if run is None:
            return None
        approvals = self.store.approvals_for_run(run_id)
        control = self._controls.get(run_id)
        run["approvals"] = approvals
        run["pending_approval"] = next((a for a in approvals if a["status"] == "pending"), None)
        run["pause_requested"] = bool(control and control.pause_requested)
        run["live"] = control is not None
        return run

    def recent(self, limit: int = 20) -> list[dict]:
        return self.store.list_runs(limit)
