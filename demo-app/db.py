"""SQLite storage, seed data, and business rules for the synthetic recruitment app.

All data here is synthetic. SQLite is the source of truth; the web layer in
main.py only renders it and calls the functions below.
"""

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR.parent / ".env")

DB_PATH = Path(os.getenv("DEMO_DB_PATH", APP_DIR / "data" / "recruit.db"))

STATUSES = ["Applied", "Screening", "Shortlisted", "Interview Scheduled", "Rejected"]
INTERVIEWERS = ["Anita Rao", "Vikram Desai", "Meera Iyer"]
DURATIONS = [30, 45, 60]
WORK_START, WORK_END = "09:00", "18:00"
FAILURE_MODES = ["before_write", "after_write"]

JOBS = [
    (1, "AI Engineer", "Bengaluru", "Build and ship LLM-powered features."),
    (2, "Full Stack Engineer", "Remote", "Own features across a React and Python stack."),
]

# (id, job_id, name, email, phone, years, company, skills, summary, status, applied_on)
CANDIDATES = [
    (1, 1, "Rahul Sharma", "rahul.sharma@example.com", "+91-90000-00001", 4, "Northwind Labs",
     "Python, PyTorch, LangChain", "Built retrieval pipelines for a support chatbot.", "Shortlisted", "2026-09-20"),
    (2, 1, "Priya Mehta", "priya.mehta@example.com", "+91-90000-00002", 6, "Contoso AI",
     "Python, LLM evaluation, FastAPI", "Led an evaluation framework for a ranking model.", "Shortlisted", "2026-09-21"),
    (3, 1, "Ankit Verma", "ankit.verma@example.com", "+91-90000-00003", 2, "Fabrikam Data",
     "Python, scikit-learn, SQL", "Data scientist moving into applied ML.", "Screening", "2026-09-23"),
    (4, 1, "Kavya Nair", "kavya.nair@example.com", "+91-90000-00004", 5, "Tailspin Systems",
     "Python, LangGraph, Docker", "Shipped agent workflows for internal ops.", "Shortlisted", "2026-09-24"),
    (5, 1, "Dev Malhotra", "dev.malhotra@example.com", "+91-90000-00005", 1, "Adatum Soft",
     "Java, Spring", "Backend developer with limited ML exposure.", "Rejected", "2026-09-18"),
    (6, 2, "Neha Patel", "neha.patel@example.com", "+91-90000-00006", 5, "Litware Web",
     "React, TypeScript, Node.js", "Full stack developer focused on dashboards.", "Shortlisted", "2026-09-22"),
    (7, 2, "Arjun Singh", "arjun.singh@example.com", "+91-90000-00007", 7, "Wingtip Cloud",
     "Next.js, Python, PostgreSQL", "Tech lead on a B2B SaaS product.", "Interview Scheduled", "2026-09-19"),
    (8, 2, "Sana Khan", "sana.khan@example.com", "+91-90000-00008", 3, "Proseware Apps",
     "Vue, Django, AWS", "Full stack engineer at a logistics startup.", "Applied", "2026-09-26"),
]

# (id, candidate_id, interviewer, date, start, duration)
SEED_INTERVIEWS = [(1, 7, "Anita Rao", "2026-10-02", "14:00", 60)]

SCHEMA = """
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, location TEXT NOT NULL, description TEXT NOT NULL);
CREATE TABLE candidates (
    id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
    name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
    years_experience INTEGER NOT NULL, current_company TEXT NOT NULL,
    skills TEXT NOT NULL, summary TEXT NOT NULL,
    status TEXT NOT NULL, applied_on TEXT NOT NULL);
CREATE TABLE interviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    interviewer TEXT NOT NULL, date TEXT NOT NULL, start_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled',
    created_at TEXT NOT NULL);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class AppError(Exception):
    """A business-rule or simulated failure, carrying the HTTP status to return."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _env_failure() -> tuple[int, str]:
    """Initial failure arming from SIMULATE_CALENDAR_FAILURE / CALENDAR_FAILURE_MODE."""
    on = os.getenv("SIMULATE_CALENDAR_FAILURE", "false").strip().lower() in ("1", "true", "yes")
    mode = os.getenv("CALENDAR_FAILURE_MODE", "before_write").strip()
    return (1 if on else 0), (mode if mode in FAILURE_MODES else "before_write")


def reset_db() -> None:
    """Drop everything and re-seed. Failure simulation is re-armed from the environment."""
    with connect() as conn:
        for table in ("interviews", "candidates", "jobs", "settings"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO jobs VALUES (?,?,?,?)", JOBS)
        conn.executemany("INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?,?)", CANDIDATES)
        for iid, cid, who, date, start, dur in SEED_INTERVIEWS:
            conn.execute(
                "INSERT INTO interviews (id, candidate_id, interviewer, date, start_time,"
                " duration_minutes, status, created_at) VALUES (?,?,?,?,?,?, 'scheduled', ?)",
                (iid, cid, who, date, start, dur, "2026-09-25T10:00:00"),
            )
        remaining, mode = _env_failure()
        set_failure(conn, remaining, mode)


def init_db() -> None:
    """Seed on first run only, so data survives restarts."""
    exists = DB_PATH.exists()
    if exists:
        with connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
            ).fetchone()
            exists = row is not None
    if not exists:
        reset_db()


# ---- failure simulation ----------------------------------------------------

def get_failure(conn: sqlite3.Connection) -> dict:
    rows = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    return {
        "remaining": int(rows.get("failure_remaining", "0")),
        "mode": rows.get("failure_mode", "before_write"),
    }


def set_failure(conn: sqlite3.Connection, remaining: int, mode: str) -> None:
    if mode not in FAILURE_MODES:
        raise AppError(f"Unknown failure mode '{mode}'.")
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('failure_remaining', ?)", (str(remaining),))
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('failure_mode', ?)", (mode,))


# ---- queries ---------------------------------------------------------------

def list_jobs(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT j.*, COUNT(c.id) AS candidate_count,"
        " SUM(c.status = 'Shortlisted') AS shortlisted_count"
        " FROM jobs j LEFT JOIN candidates c ON c.job_id = j.id GROUP BY j.id ORDER BY j.id"
    ).fetchall()


def get_job(conn, job_id: int):
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_candidates(conn, job_id: int, status: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM candidates WHERE job_id = ?"
    args: list = [job_id]
    if status:
        sql += " AND status = ?"
        args.append(status)
    return conn.execute(sql + " ORDER BY id", args).fetchall()


def get_candidate(conn, candidate_id: int):
    return conn.execute(
        "SELECT c.*, j.title AS job_title FROM candidates c JOIN jobs j ON j.id = c.job_id"
        " WHERE c.id = ?",
        (candidate_id,),
    ).fetchone()


def list_interviews(conn, date: str | None = None, candidate_id: int | None = None):
    sql = (
        "SELECT i.*, c.name AS candidate_name, j.title AS job_title FROM interviews i"
        " JOIN candidates c ON c.id = i.candidate_id JOIN jobs j ON j.id = c.job_id WHERE 1=1"
    )
    args: list = []
    if date:
        sql += " AND i.date = ?"
        args.append(date)
    if candidate_id:
        sql += " AND i.candidate_id = ?"
        args.append(candidate_id)
    return conn.execute(sql + " ORDER BY i.date, i.start_time, i.id", args).fetchall()


# ---- commands --------------------------------------------------------------

def update_status(conn, candidate_id: int, status: str) -> sqlite3.Row:
    if status not in STATUSES:
        raise AppError(f"Invalid status '{status}'.")
    if get_candidate(conn, candidate_id) is None:
        raise AppError("Candidate not found.", 404)
    conn.execute("UPDATE candidates SET status = ? WHERE id = ?", (status, candidate_id))
    return get_candidate(conn, candidate_id)


def _parse_slot(date: str, start: str, duration: int) -> tuple[datetime, datetime]:
    try:
        begin = datetime.strptime(f"{date} {start}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise AppError("Enter a valid date (YYYY-MM-DD) and start time (HH:MM).") from None
    if duration not in DURATIONS:
        raise AppError(f"Duration must be one of {', '.join(map(str, DURATIONS))} minutes.")
    end = begin + timedelta(minutes=duration)
    day = begin.strftime("%Y-%m-%d")
    if begin < datetime.strptime(f"{day} {WORK_START}", "%Y-%m-%d %H:%M") or end > datetime.strptime(
        f"{day} {WORK_END}", "%Y-%m-%d %H:%M"
    ):
        raise AppError(f"Interviews must fall within working hours ({WORK_START}-{WORK_END}).")
    return begin, end


def create_interview(
    conn, candidate_id: int, interviewer: str, date: str, start: str, duration: int
) -> int:
    """Validate, apply the simulated calendar failure if armed, then insert.

    Validation runs first so a rejected request never consumes an armed failure.
    """
    cand = get_candidate(conn, candidate_id)
    if cand is None:
        raise AppError("Candidate not found.", 404)
    if cand["status"] == "Rejected":
        raise AppError("Cannot schedule an interview for a rejected candidate.", 409)
    if interviewer not in INTERVIEWERS:
        raise AppError("Select a valid interviewer.")
    begin, end = _parse_slot(date, start, duration)

    if any(i["status"] == "scheduled" for i in list_interviews(conn, candidate_id=candidate_id)):
        raise AppError(f"{cand['name']} already has a scheduled interview.", 409)
    for other in list_interviews(conn, date=date):
        if other["status"] != "scheduled" or other["interviewer"] != interviewer:
            continue
        o_begin = datetime.strptime(f"{other['date']} {other['start_time']}", "%Y-%m-%d %H:%M")
        o_end = o_begin + timedelta(minutes=other["duration_minutes"])
        if begin < o_end and o_begin < end:
            raise AppError(
                f"{interviewer} is already booked {other['start_time']}-{o_end:%H:%M} on {date}.", 409
            )

    failure = get_failure(conn)
    armed = failure["remaining"] > 0
    if armed:
        set_failure(conn, failure["remaining"] - 1, failure["mode"])
        if failure["mode"] == "before_write":
            conn.commit()  # persist the consumed failure; nothing was written
            raise AppError("Calendar service unavailable (simulated). Interview was not created.", 503)

    cur = conn.execute(
        "INSERT INTO interviews (candidate_id, interviewer, date, start_time, duration_minutes,"
        " status, created_at) VALUES (?,?,?,?,?, 'scheduled', ?)",
        (candidate_id, interviewer, date, start, duration, datetime.now().isoformat(timespec="seconds")),
    )
    if armed and failure["mode"] == "after_write":
        conn.commit()  # the row IS saved, but the caller is told it failed
        raise AppError("Calendar sync timed out (simulated). Please check the calendar.", 503)
    return cur.lastrowid


def cancel_interview(conn, interview_id: int) -> None:
    row = conn.execute("SELECT * FROM interviews WHERE id = ?", (interview_id,)).fetchone()
    if row is None:
        raise AppError("Interview not found.", 404)
    conn.execute("UPDATE interviews SET status = 'cancelled' WHERE id = ?", (interview_id,))
