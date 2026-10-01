"""Synthetic recruitment application.

A small FastAPI + Jinja2 app that simulates a recruiting system: jobs,
candidates, interviews, and a chaos-mode switch that makes interview creation
fail so browser automation can be exercised against a realistic outage.

Run locally:
    cd demo-app && uvicorn app.main:app --port 5000
"""

from datetime import datetime

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .models import CANDIDATE_STATUSES
from .store import FAILURE_MODES, InterviewServiceError, store

app = FastAPI(title="Synthetic Recruitment App", version="0.1.0")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> RedirectResponse:
    return RedirectResponse(url="/jobs", status_code=302)


@app.get("/jobs", response_class=HTMLResponse)
def jobs(request: Request) -> HTMLResponse:
    jobs = []
    for job in store.list_jobs():
        jobs.append(
            {
                **job,
                "candidate_count": len(store.list_candidates(job["id"])),
            }
        )
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"jobs": jobs, "now": _now()},
    )


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str) -> HTMLResponse:
    job = store.get_job(job_id)
    if job is None:
        return templates.TemplateResponse(
            request, "error.html", {"message": f"Job '{job_id}' not found"}, status_code=404
        )
    status_filter = request.query_params.get("status", "").strip()
    query = request.query_params.get("q", "").strip().lower()
    candidates = store.list_candidates(job_id)
    if status_filter:
        candidates = [c for c in candidates if c["status"] == status_filter]
    if query:
        candidates = [
            c
            for c in candidates
            if query in c["name"].lower()
            or query in c["email"].lower()
            or any(query in s.lower() for s in c["skills"])
        ]
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "job": job,
            "candidates": candidates,
            "status_filter": status_filter,
            "query": query,
            "statuses": CANDIDATE_STATUSES,
            "now": _now(),
        },
    )


@app.get("/candidates/{candidate_id}", response_class=HTMLResponse)
def candidate_detail(request: Request, candidate_id: str) -> HTMLResponse:
    candidate = store.get_candidate(candidate_id)
    if candidate is None:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": f"Candidate '{candidate_id}' not found"},
            status_code=404,
        )
    job = store.get_job(candidate["job_id"])
    interviews = store.interviews_for_candidate(candidate_id)
    return templates.TemplateResponse(
        request,
        "candidate_detail.html",
        {
            "candidate": candidate,
            "job": job,
            "interviews": interviews,
            "statuses": CANDIDATE_STATUSES,
            "now": _now(),
        },
    )


@app.post("/candidates/{candidate_id}/status", response_class=HTMLResponse)
def update_candidate_status(request: Request, candidate_id: str, status: str = Form(...)) -> RedirectResponse:
    if status not in CANDIDATE_STATUSES:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": f"Invalid status '{status}'"},
            status_code=400,
        )
    candidate = store.update_candidate_status(candidate_id, status)
    if candidate is None:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": f"Candidate '{candidate_id}' not found"},
            status_code=404,
        )
    return RedirectResponse(
        url=f"/candidates/{candidate_id}?flash=Status+updated+to+{status}",
        status_code=303,
    )


@app.get("/candidates/{candidate_id}/interviews/new", response_class=HTMLResponse)
def new_interview_form(request: Request, candidate_id: str) -> HTMLResponse:
    candidate = store.get_candidate(candidate_id)
    if candidate is None:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": f"Candidate '{candidate_id}' not found"},
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "interview_form.html",
        {"candidate": candidate, "now": _now()},
    )


@app.post("/candidates/{candidate_id}/interviews", response_class=HTMLResponse)
def create_interview(
    request: Request,
    candidate_id: str,
    round_name: str = Form(...),
    scheduled_at: str = Form(...),
    interviewer: str = Form(...),
) -> HTMLResponse:
    candidate = store.get_candidate(candidate_id)
    if candidate is None:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": f"Candidate '{candidate_id}' not found"},
            status_code=404,
        )
    try:
        datetime.fromisoformat(scheduled_at)
    except ValueError:
        return templates.TemplateResponse(
            request,
            "interview_form.html",
            {
                "candidate": candidate,
                "error": "Scheduled time must be a valid date/time",
                "now": _now(),
            },
            status_code=400,
        )
    try:
        interview = store.create_interview(
            candidate_id=candidate_id,
            job_id=candidate["job_id"],
            round_name=round_name,
            scheduled_at=scheduled_at,
            interviewer=interviewer,
        )
    except InterviewServiceError as exc:
        # Simulated outage: render a 500 error page the automation can detect.
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": str(exc), "status_code": 500},
            status_code=500,
        )
    return RedirectResponse(
        url=f"/candidates/{candidate_id}?flash=Interview+scheduled",
        status_code=303,
    )


@app.get("/interviews", response_class=HTMLResponse)
def interviews(request: Request) -> HTMLResponse:
    rows = []
    for interview in store.list_interviews():
        candidate = store.get_candidate(interview["candidate_id"])
        job = store.get_job(interview["job_id"])
        rows.append(
            {
                **interview,
                "candidate_name": candidate["name"] if candidate else "unknown",
                "job_title": job["title"] if job else "unknown",
            }
        )
    return templates.TemplateResponse(
        request,
        "interviews.html",
        {"interviews": rows, "chaos_mode": store.chaos_mode, "failure": store.failure_state(), "now": _now()},
    )


@app.post("/admin/chaos", response_class=HTMLResponse)
def toggle_chaos(request: Request, enable: str = Form(...)) -> RedirectResponse:
    store.chaos_mode = enable == "1"
    return RedirectResponse(url="/interviews", status_code=303)


@app.post("/admin/failure")
def arm_failure(remaining: int = Form(...), mode: str = Form("before_write")) -> RedirectResponse:
    """Demo harness: make the next `remaining` interview creates fail in `mode`."""
    if mode not in FAILURE_MODES:
        return JSONResponse({"error": f"mode must be one of {FAILURE_MODES}"}, status_code=400)
    store.set_failure_plan(remaining, mode)
    return RedirectResponse(url="/interviews", status_code=303)


@app.post("/admin/reset")
def reset_data() -> RedirectResponse:
    """Demo harness: restore seed data and the environment's failure plan."""
    store.reset()
    return RedirectResponse(url="/jobs", status_code=303)


@app.get("/api/state")
def api_state() -> JSONResponse:
    """Read-only JSON snapshot for independent verification (not for performing actions)."""
    return JSONResponse({
        "candidates": store.list_candidates_all(),
        "interviews": store.list_interviews(),
        "failure": store.failure_state(),
    })


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
