"""Pytest fixtures: boot the synthetic recruitment app and manage the browser."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
DEMO_APP_DIR = ROOT_DIR / "demo-app"
VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
TEST_PORT = 5055


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"Demo app did not start listening on {host}:{port} within {timeout}s")


@pytest.fixture(scope="session")
def demo_app_url() -> str:
    """Start the synthetic recruitment app for the test session."""
    env = {**os.environ, "PYTHONPATH": str(DEMO_APP_DIR)}
    log_path = Path("/tmp/opspilot-demoapp-test.log")
    log_file = open(log_path, "w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(TEST_PORT),
        ],
        cwd=DEMO_APP_DIR,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        _wait_for_port("127.0.0.1", TEST_PORT)
        yield f"http://127.0.0.1:{TEST_PORT}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(autouse=True)
def _reset_app_state(demo_app_url: str):
    """Reset the demo app to seed state before each test for isolation."""
    import urllib.request

    req = urllib.request.Request(
        f"{demo_app_url}/admin/reset",
        data=b"",
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()


@pytest.fixture()
def browser(demo_app_url: str):
    """Provide a fresh RecruitmentBrowser per test."""
    from automation import RecruitmentBrowser

    browser = RecruitmentBrowser(base_url=demo_app_url, headless=True)
    browser.launch()
    try:
        yield browser
    finally:
        browser.close()
