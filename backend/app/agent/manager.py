"""Starts agent runs in the background and serves their live state.

One run at a time: two runs operating the same app concurrently would make each
other's state checks meaningless. Live events are served from memory while a
run is in progress; the finished run (with its full event log) is stored in SQLite.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Callable

from ..config import settings
from ..database import SessionLocal
from ..models import AgentRun
from .driver import BrowserDriver, HarnessClient
from .events import RunLog
from .runner import AgentOptions, RunResult, run_goal


class RunInProgress(Exception):
    pass


def _default_tools():
    harness = HarnessClient.connect(settings.demo_app_url, settings.demo_app_timeout_seconds)
    driver = BrowserDriver.launch(settings.demo_app_url, headless=settings.agent_headless)
    return driver, harness


class RunManager:
    def __init__(self, tools_factory: Callable = _default_tools, options: AgentOptions | None = None):
        self.tools_factory = tools_factory
        self.options = options or AgentOptions(
            max_attempts=settings.agent_max_attempts,
            retry_backoff_seconds=settings.agent_retry_backoff_seconds,
            step_delay_seconds=settings.agent_step_delay_seconds,
        )
        self._live: dict[str, RunLog] = {}
        self._lock = threading.Lock()
        self._active: str | None = None

    def start(self, goal: str, scenario: str | None, reset_demo_data: bool) -> str:
        with self._lock:
            if self._active:
                raise RunInProgress(self._active)
            run_id = uuid.uuid4().hex[:12]
            self._active = run_id
            self._live[run_id] = RunLog(run_id)
        with SessionLocal() as db:
            db.add(AgentRun(id=run_id, goal=goal, scenario=scenario, status="running"))
            db.commit()
        threading.Thread(target=self._execute, args=(run_id, goal, scenario, reset_demo_data),
                         name=f"agent-run-{run_id}", daemon=True).start()
        return run_id

    def _execute(self, run_id: str, goal: str, scenario: str | None, reset: bool) -> None:
        log = self._live[run_id]
        driver = harness = None
        try:
            # Playwright's sync API is bound to the thread that launched it, so launch here.
            driver, harness = self.tools_factory()
            result = run_goal(goal, driver, harness, log, scenario=scenario,
                              reset_demo_data=reset, options=self.options)
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
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            run.status, run.summary, run.blocker = result.status, result.summary, result.blocker
            run.details, run.events = result.details, log.events
            run.finished_at = datetime.now(timezone.utc)
            db.commit()
        with self._lock:
            self._active = None
            self._live.pop(run_id, None)

    def get(self, run_id: str) -> dict | None:
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            if run is None:
                return None
            data = serialize(run)
        live = self._live.get(run_id)
        if live is not None and data["status"] == "running":
            data["events"] = live.events
        return data

    def recent(self, limit: int = 20) -> list[dict]:
        with SessionLocal() as db:
            runs = db.query(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit).all()
            return [{k: v for k, v in serialize(r).items() if k != "events"} for r in runs]


def serialize(run: AgentRun) -> dict:
    return {
        "id": run.id, "goal": run.goal, "scenario": run.scenario, "status": run.status,
        "summary": run.summary, "blocker": run.blocker, "details": run.details or {},
        "events": run.events or [],
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
