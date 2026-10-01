"""Fixtures: run the real demo recruitment app in-process and point the agent's driver at it."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "demo-app"))

import db as demo_db  # noqa: E402  (demo-app/db.py)

from app.agent.driver import RecruitAppDriver  # noqa: E402
from app.agent.events import RunLog  # noqa: E402


@pytest.fixture()
def demo_client(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_db, "DB_PATH", tmp_path / "recruit.db")
    monkeypatch.delenv("SIMULATE_CALENDAR_FAILURE", raising=False)
    from main import app as demo_app  # demo-app/main.py

    with TestClient(demo_app, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def driver(demo_client):
    return RecruitAppDriver(demo_client)


@pytest.fixture()
def log():
    return RunLog("test-run")


def interviews_for(candidate_id: int) -> list[dict]:
    with demo_db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM interviews WHERE candidate_id = ? ORDER BY id", (candidate_id,))]
