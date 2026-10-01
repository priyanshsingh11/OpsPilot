import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import models  # noqa: F401  (registers tables with Base before init_db)
from .agent.manager import RunInProgress, RunManager
from .agent.scenarios import SCENARIOS
from .config import settings
from .database import check_db, init_db

# Structured agent events are logged as one JSON line each (see app/agent/events.py).
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    db_ok = check_db()
    return {
        "status": "ok" if db_ok else "degraded",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "database": "ok" if db_ok else "error",
    }


runs = RunManager()


class RunRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=500)
    # Demo harness: arm a reproducible failure before the run (None = leave the app as is).
    scenario: str | None = None
    reset_demo_data: bool = False


@app.get("/api/scenarios")
def list_scenarios() -> list[dict]:
    return [{"key": s.key, "label": s.label, "description": s.description} for s in SCENARIOS.values()]


@app.post("/api/runs", status_code=202)
def start_run(req: RunRequest) -> dict:
    if req.scenario is not None and req.scenario not in SCENARIOS:
        raise HTTPException(400, f"Unknown scenario '{req.scenario}'")
    try:
        run_id = runs.start(req.goal.strip(), req.scenario, req.reset_demo_data)
    except RunInProgress as exc:
        raise HTTPException(409, f"Run {exc} is still in progress") from None
    return {"id": run_id}


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return runs.recent()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run
