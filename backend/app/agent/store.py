"""Persistence for runs and approvals. Returns plain dicts so callers never hold DB sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from ..models import AgentRun, Approval

ACTIVE_STATUSES = ("running", "paused", "awaiting_approval")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_run(run: AgentRun) -> dict:
    return {
        "id": run.id, "goal": run.goal, "scenario": run.scenario, "status": run.status,
        "summary": run.summary, "blocker": run.blocker, "details": run.details or {},
        "events": run.events or [], "checkpoint": run.checkpoint or {},
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def serialize_approval(a: Approval) -> dict:
    return {
        "id": a.id, "run_id": a.run_id, "action": a.action, "status": a.status,
        "payload": a.payload, "payload_hash": a.payload_hash, "details": a.details or {},
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
    }


class RunStore:
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    # ---- runs
    def create_run(self, run_id: str, goal: str, scenario: str | None) -> None:
        with self.session_factory() as db:
            db.add(AgentRun(id=run_id, goal=goal, scenario=scenario, status="running"))
            db.commit()

    def get_run(self, run_id: str) -> dict | None:
        with self.session_factory() as db:
            run = db.get(AgentRun, run_id)
            return serialize_run(run) if run else None

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self.session_factory() as db:
            runs = db.query(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit).all()
            return [{k: v for k, v in serialize_run(r).items() if k != "events"} for r in runs]

    def set_status(self, run_id: str, status: str) -> None:
        with self.session_factory() as db:
            db.get(AgentRun, run_id).status = status
            db.commit()

    def save_events(self, run_id: str, events: list[dict]) -> None:
        with self.session_factory() as db:
            db.get(AgentRun, run_id).events = events
            db.commit()

    def save_checkpoint(self, run_id: str, checkpoint: dict) -> None:
        with self.session_factory() as db:
            db.get(AgentRun, run_id).checkpoint = checkpoint
            db.commit()

    def finish_run(self, run_id: str, status: str, summary: str | None, blocker: str | None,
                   details: dict, events: list[dict]) -> None:
        with self.session_factory() as db:
            run = db.get(AgentRun, run_id)
            run.status, run.summary, run.blocker = status, summary, blocker
            run.details, run.events, run.finished_at = details, events, _now()
            db.commit()

    def mark_interrupted(self) -> int:
        """Runs still marked active when the process starts lost their thread; make them resumable."""
        with self.session_factory() as db:
            runs = db.query(AgentRun).filter(AgentRun.status.in_(ACTIVE_STATUSES)).all()
            for run in runs:
                run.status = "interrupted"
            db.commit()
            return len(runs)

    def reopen_run(self, run_id: str) -> None:
        with self.session_factory() as db:
            run = db.get(AgentRun, run_id)
            run.status, run.finished_at = "running", None
            db.commit()

    # ---- approvals
    def create_approval(self, run_id: str, action: str, payload: dict, payload_hash: str,
                        details: dict) -> dict:
        with self.session_factory() as db:
            approval = Approval(id=uuid.uuid4().hex[:12], run_id=run_id, action=action, payload=payload,
                                payload_hash=payload_hash, details=details)
            db.add(approval)
            db.commit()
            return serialize_approval(approval)

    def get_approval(self, approval_id: str) -> dict | None:
        with self.session_factory() as db:
            a = db.get(Approval, approval_id)
            return serialize_approval(a) if a else None

    def approvals_for_run(self, run_id: str) -> list[dict]:
        with self.session_factory() as db:
            rows = db.query(Approval).filter(Approval.run_id == run_id).order_by(Approval.created_at).all()
            return [serialize_approval(a) for a in rows]

    def find_approval(self, run_id: str, action: str, payload_hash: str) -> dict | None:
        """The latest usable approval for exactly this payload (so a resumed run reuses it)."""
        with self.session_factory() as db:
            a = (db.query(Approval)
                 .filter(Approval.run_id == run_id, Approval.action == action,
                         Approval.payload_hash == payload_hash, Approval.status != "cancelled")
                 .order_by(Approval.created_at.desc()).first())
            return serialize_approval(a) if a else None

    def decide(self, approval_id: str, decision: str) -> bool:
        """Approve or reject. Only a pending approval can be decided, and only once."""
        with self.session_factory() as db:
            a = db.get(Approval, approval_id)
            if a is None or a.status != "pending":
                return False
            a.status, a.decided_at = decision, _now()
            db.commit()
            return True

    def cancel_pending(self, run_id: str) -> None:
        with self.session_factory() as db:
            for a in db.query(Approval).filter(Approval.run_id == run_id, Approval.status == "pending"):
                a.status, a.decided_at = "cancelled", _now()
            db.commit()
