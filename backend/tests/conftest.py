"""Fixtures: boot the real synthetic recruitment app and drive it with a real browser.

The demo app (demo-app/app) runs in a uvicorn subprocess; the agent operates it
through Playwright Chromium via the automation layer, exactly as in a live run.
Each test starts from seed data with no failure armed.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.append(str(ROOT))  # top-level `automation` package

from app.agent.driver import BrowserDriver, HarnessClient  # noqa: E402
from app.agent.events import RunLog  # noqa: E402

DEMO_APP_DIR = ROOT / "demo-app"
TEST_PORT = 5065


def _wait_for_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"Demo app did not start on port {port}")


@pytest.fixture(scope="session")
def demo_app_url():
    env = {k: v for k, v in os.environ.items() if not k.startswith("DEMO_FAIL")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(TEST_PORT)],
        cwd=DEMO_APP_DIR, env={**env, "PYTHONPATH": str(DEMO_APP_DIR)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(TEST_PORT)
        yield f"http://127.0.0.1:{TEST_PORT}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def browser_driver(demo_app_url, tmp_path_factory):
    driver = BrowserDriver.launch(demo_app_url, screenshot_dir=str(tmp_path_factory.mktemp("shots")))
    yield driver
    driver.close()


@pytest.fixture()
def harness(demo_app_url):
    h = HarnessClient.connect(demo_app_url)
    h.reset_demo_data()
    yield h
    h.close()


@pytest.fixture()
def driver(browser_driver, harness):
    return browser_driver


@pytest.fixture()
def log():
    return RunLog("test-run")


def interviews_for(harness: HarnessClient, candidate_id: str, round_name: str | None = None) -> list[dict]:
    return [i for i in harness.source_of_truth()["interviews"]
            if i["candidate_id"] == candidate_id and (round_name is None or i["round"] == round_name)]


@pytest.fixture()
def api_env(demo_app_url, tmp_path, monkeypatch):
    """The real FastAPI app on an isolated SQLite file, driving a real browser against the demo app."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import database, main
    from app.agent import manager
    from app.agent.runner import AgentOptions

    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "engine", engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def tools():  # a fresh browser in the run's own thread, like production
        return (BrowserDriver.launch(demo_app_url, screenshot_dir=str(tmp_path)),
                HarnessClient.connect(demo_app_url))

    options = AgentOptions(retry_backoff_seconds=0)
    monkeypatch.setattr(main, "runs", manager.RunManager(tools, options, session_factory))
    database.Base.metadata.create_all(bind=engine)
    with TestClient(main.app) as client:
        yield SimpleNamespace(client=client, tools=tools, options=options, session_factory=session_factory)


@pytest.fixture()
def api(api_env):
    return api_env.client
