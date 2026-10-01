"""Synthetic recruitment portal: the controlled app OpsPilot operates through a browser.

Run (from repo root):  .venv/bin/uvicorn main:app --app-dir demo-app --port 5001
"""

from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Synthetic Recruitment Portal", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=db.APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=db.APP_DIR / "templates")


def render(request: Request, name: str, status_code: int = 200, **ctx) -> HTMLResponse:
    ctx.setdefault("msg", request.query_params.get("msg"))
    ctx.setdefault("kind", request.query_params.get("kind", "success"))
    ctx.update(statuses=db.STATUSES, interviewers=db.INTERVIEWERS, durations=db.DURATIONS)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def redirect(path: str, msg: str, kind: str = "success") -> RedirectResponse:
    return RedirectResponse(f"{path}{'&' if '?' in path else '?'}{urlencode({'msg': msg, 'kind': kind})}", 303)


@app.exception_handler(db.AppError)
async def app_error_handler(request: Request, exc: db.AppError):
    return render(request, "error.html", exc.status_code, msg=exc.message, kind="error")


# ---- pages -----------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def jobs_page(request: Request):
    with db.connect() as conn:
        return render(request, "jobs.html", jobs=db.list_jobs(conn))


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: int, status: str = ""):
    with db.connect() as conn:
        job = db.get_job(conn, job_id)
        if job is None:
            raise db.AppError("Job not found.", 404)
        selected = status if status in db.STATUSES else ""
        return render(
            request, "job.html", job=job, selected=selected,
            candidates=db.list_candidates(conn, job_id, selected or None),
        )


def candidate_page(request: Request, conn, candidate_id: int, status_code=200, **ctx):
    cand = db.get_candidate(conn, candidate_id)
    if cand is None:
        raise db.AppError("Candidate not found.", 404)
    return render(
        request, "candidate.html", status_code, cand=cand,
        interviews=db.list_interviews(conn, candidate_id=candidate_id), **ctx,
    )


@app.get("/candidates/{candidate_id}", response_class=HTMLResponse)
def candidate_detail(request: Request, candidate_id: int):
    with db.connect() as conn:
        return candidate_page(request, conn, candidate_id)


@app.post("/candidates/{candidate_id}/status")
def set_status(candidate_id: int, status: str = Form(...)):
    with db.connect() as conn:
        cand = db.update_status(conn, candidate_id, status)
    return redirect(f"/candidates/{candidate_id}", f"Status for {cand['name']} updated to {status}.")


@app.post("/candidates/{candidate_id}/interviews")
def schedule_interview(
    request: Request, candidate_id: int,
    interviewer: str = Form(...), date: str = Form(...),
    start_time: str = Form(...), duration_minutes: int = Form(...),
):
    with db.connect() as conn:
        try:
            db.create_interview(conn, candidate_id, interviewer, date, start_time, duration_minutes)
        except db.AppError as exc:
            # Re-render the candidate page (not a redirect) so the error banner and
            # the user's form values stay visible, with the real HTTP status.
            form = dict(interviewer=interviewer, date=date, start_time=start_time, duration=duration_minutes)
            return candidate_page(request, conn, candidate_id, exc.status_code,
                                  msg=exc.message, kind="error", form=form)
        cand = db.get_candidate(conn, candidate_id)
    return redirect(
        f"/candidates/{candidate_id}",
        f"Interview scheduled for {cand['name']} on {date} at {start_time}.",
    )


@app.get("/interviews", response_class=HTMLResponse)
def interviews_page(request: Request, date: str = ""):
    with db.connect() as conn:
        rows = db.list_interviews(conn, date=date or None)
    days: dict[str, list] = {}
    for r in rows:
        days.setdefault(r["date"], []).append(r)
    return render(request, "interviews.html", days=days, filter_date=date, total=len(rows))


@app.post("/interviews/{interview_id}/cancel")
def cancel(interview_id: int):
    with db.connect() as conn:
        db.cancel_interview(conn, interview_id)
    return redirect("/interviews", f"Interview {interview_id} cancelled.")


# ---- admin: failure simulation + reset -------------------------------------

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    with db.connect() as conn:
        return render(request, "admin.html", failure=db.get_failure(conn), modes=db.FAILURE_MODES)


@app.post("/admin/failure")
def admin_failure(mode: str = Form("before_write"), remaining: int = Form(...)):
    with db.connect() as conn:
        db.set_failure(conn, max(remaining, 0), mode)
    return redirect("/admin", f"Calendar failure set: {remaining} upcoming attempt(s), mode {mode}.")


@app.post("/admin/reset")
def admin_reset():
    db.reset_db()
    return redirect("/admin", "Demo data reset to seed state.")


# ---- JSON state, for verification code (not for the agent's browser actions)

@app.get("/api/state")
def api_state():
    with db.connect() as conn:
        return JSONResponse({
            "candidates": [dict(r) for r in conn.execute("SELECT * FROM candidates ORDER BY id")],
            "interviews": [dict(r) for r in db.list_interviews(conn)],
            "failure": db.get_failure(conn),
        })
