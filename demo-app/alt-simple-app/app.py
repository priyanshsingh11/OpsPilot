"""Synthetic recruitment app (ATS). Server-rendered so a browser can operate it."""
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

BASE = Path(__file__).parent
DB_PATH = BASE / "recruitment.db"
WORK_START, WORK_END = "09:00", "18:00"
templates = Jinja2Templates(directory=str(BASE / "templates"))
app = FastAPI(title="Recruitment Demo App")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def seed() -> None:
    DB_PATH.unlink(missing_ok=True)
    tomorrow = date.today() + timedelta(days=1)
    friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)
    with db() as c:
        c.executescript(
            """
            CREATE TABLE jobs(id INTEGER PRIMARY KEY, title TEXT, interviewer TEXT);
            CREATE TABLE candidates(id INTEGER PRIMARY KEY, name TEXT, email TEXT,
                job_id INTEGER, status TEXT);
            CREATE TABLE interviews(id INTEGER PRIMARY KEY, candidate_id INTEGER,
                interviewer TEXT, day TEXT, start TEXT, duration INTEGER);
            """
        )
        c.executemany("INSERT INTO jobs VALUES(?,?,?)", [
            (1, "AI Engineer", "Priya Nair"),
            (2, "Full Stack Engineer", "Sam Okafor"),
        ])
        c.executemany("INSERT INTO candidates VALUES(?,?,?,?,?)", [
            (1, "Aisha Khan", "aisha@example.test", 1, "Shortlisted"),
            (2, "Rohan Mehta", "rohan@example.test", 1, "Shortlisted"),
            (3, "Lena Fischer", "lena@example.test", 1, "Shortlisted"),
            (4, "Diego Alvarez", "diego@example.test", 1, "Applied"),
            (5, "Mei Tanaka", "mei@example.test", 1, "Rejected"),
            (6, "Omar Haddad", "omar@example.test", 1, "Interview Scheduled"),
            (7, "Tom Becker", "tom@example.test", 2, "Shortlisted"),
            (8, "Nora Ivanova", "nora@example.test", 2, "Shortlisted"),
            (9, "Chris Lee", "chris@example.test", 2, "Applied"),
            (10, "Fatima Noor", "fatima@example.test", 2, "Shortlisted"),
            (11, "Jon Weber", "jon@example.test", 2, "Interview Scheduled"),
        ])
        # Pre-existing bookings so slot-finding has real conflicts to avoid.
        c.executemany("INSERT INTO interviews(candidate_id,interviewer,day,start,duration) VALUES(?,?,?,?,?)", [
            (6, "Priya Nair", tomorrow.isoformat(), "13:00", 60),
            (11, "Sam Okafor", friday.isoformat(), "14:00", 60),
        ])


if not DB_PATH.exists():
    seed()


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def candidate_ctx(c, cid: int, error: str | None = None, msg: str | None = None):
    cand = c.execute(
        "SELECT c.*, j.title job, j.interviewer FROM candidates c JOIN jobs j ON j.id=c.job_id WHERE c.id=?",
        (cid,)).fetchone()
    ivs = c.execute("SELECT * FROM interviews WHERE candidate_id=? ORDER BY day,start", (cid,)).fetchall()
    return {"cand": cand, "interviews": ivs, "error": error, "msg": msg}


@app.get("/")
def index():
    return RedirectResponse("/candidates")


@app.get("/candidates", response_class=HTMLResponse)
def candidates(request: Request, job: str = "", status: str = ""):
    q = "SELECT c.*, j.title job FROM candidates c JOIN jobs j ON j.id=c.job_id WHERE 1=1"
    args: list = []
    if job:
        q += " AND j.title=?"; args.append(job)
    if status:
        q += " AND c.status=?"; args.append(status)
    with db() as c:
        rows = c.execute(q + " ORDER BY c.id", args).fetchall()
        jobs = c.execute("SELECT title FROM jobs").fetchall()
    return templates.TemplateResponse(request, "candidates.html", {
        "rows": rows, "jobs": jobs, "job": job, "status": status,
        "statuses": ["Applied", "Shortlisted", "Interview Scheduled", "Rejected"]})


@app.get("/candidates/{cid}", response_class=HTMLResponse)
def candidate(request: Request, cid: int, msg: str | None = None):
    with db() as c:
        return templates.TemplateResponse(request, "candidate.html", candidate_ctx(c, cid, msg=msg))


@app.post("/candidates/{cid}/interviews", response_class=HTMLResponse)
def schedule(request: Request, cid: int, day: str = Form(...), start: str = Form(...), duration: int = Form(...)):
    with db() as c:
        ctx = candidate_ctx(c, cid)
        error = None
        try:
            d = date.fromisoformat(day)
            s = to_min(start)
        except ValueError:
            d, s, error = None, 0, "Invalid date or time."
        if not error and d < date.today():
            error = "Cannot schedule in the past."
        elif not error and not (15 <= duration <= 180):
            error = "Duration must be 15-180 minutes."
        elif not error and (s < to_min(WORK_START) or s + duration > to_min(WORK_END)):
            error = f"Outside working hours ({WORK_START}-{WORK_END})."
        elif not error and ctx["interviews"]:
            error = "Candidate already has an interview scheduled."
        elif not error:
            for iv in c.execute("SELECT * FROM interviews WHERE interviewer=? AND day=?",
                                (ctx["cand"]["interviewer"], day)):
                if s < to_min(iv["start"]) + iv["duration"] and to_min(iv["start"]) < s + duration:
                    error = f"Interviewer {iv['interviewer']} is busy at {iv['start']} ({iv['duration']} min)."
                    break
        if error:
            ctx["error"] = error
            return templates.TemplateResponse(request, "candidate.html", ctx, status_code=409)
        c.execute("INSERT INTO interviews(candidate_id,interviewer,day,start,duration) VALUES(?,?,?,?,?)",
                  (cid, ctx["cand"]["interviewer"], day, start, duration))
    return RedirectResponse(f"/candidates/{cid}?msg=Interview+scheduled", status_code=303)


@app.post("/candidates/{cid}/status")
def set_status(cid: int, status: str = Form(...)):
    with db() as c:
        c.execute("UPDATE candidates SET status=? WHERE id=?", (status, cid))
    return RedirectResponse(f"/candidates/{cid}?msg=Status+updated", status_code=303)


@app.get("/interviews", response_class=HTMLResponse)
def interviews(request: Request, day: str = ""):
    q = ("SELECT i.*, c.name FROM interviews i JOIN candidates c ON c.id=i.candidate_id")
    with db() as c:
        rows = c.execute(q + (" WHERE i.day=?" if day else "") + " ORDER BY i.day,i.start",
                         (day,) if day else ()).fetchall()
    return templates.TemplateResponse(request, "interviews.html", {"rows": rows, "day": day})


@app.post("/reset")
def reset():
    seed()
    return RedirectResponse("/candidates", status_code=303)
