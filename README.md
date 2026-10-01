# OpsPilot

A computer-operator prototype: receives a plain-English goal, operates
applications on a computer, and finishes the work with evidence.

- **Phase 1** — Foundation: Next.js dashboard + FastAPI backend + SQLite.
- **Phase 3** — Browser automation: Playwright layer that operates the
  synthetic recruitment app, with structured results, screenshots, and
  failure detection.
- **Phase 5** — Reliable failure recovery: the agent detects a failed action,
  checks the app's real state before retrying, never creates duplicates, and
  verifies the result or reports a concrete blocker.

## Layout
- `backend/` — FastAPI app (`app/main.py`, `app/config.py`, `app/database.py`)
- `frontend/` — Next.js dashboard; API client in `frontend/lib/api.ts`
- `demo-app/` — Synthetic recruitment app (FastAPI + Jinja2) that the
  automation layer operates
- `automation/` — Playwright browser automation layer + workflow tests
- `.env.example` — backend config (copy to `.env`); `frontend/.env.example` (copy to `frontend/.env.local`)

## Setup
Requires Python 3.11+ and Node 20+.

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.local

python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

cd frontend && npm install
```

## Run
```bash
# Terminal 1 — backend (http://localhost:8000, docs at /docs)
cd backend && ../.venv/bin/uvicorn app.main:app --reload --port 8000

# Terminal 2 — frontend (http://localhost:3000)
cd frontend && npm run dev
```

## Verify
```bash
curl http://localhost:8000/api/health
```
Open http://localhost:3000 — the badge in the header should read "Backend ok".

The SQLite file is created at `backend/opspilot.db` on first start (override with `DATABASE_URL`).

## Phase 3 — Browser Automation

The automation layer lives in `automation/`. It launches Chromium via
Playwright and operates the synthetic recruitment app in `demo-app/`.

```bash
# Terminal 1 — synthetic recruitment app (http://127.0.0.1:5000)
cd demo-app && ../.venv/bin/uvicorn app.main:app --port 5000

# Terminal 2 — run the workflow tests (boots its own app instance)
.venv/bin/python -m pytest automation/tests/ -v
```

The tests prove the layer can deterministically:
1. Find shortlisted AI Engineer candidates,
2. Create an interview for one candidate,
3. Update the candidate status,
4. Verify the interview exists,

and that it detects a simulated outage (chaos mode), exposes structured error
information, and recovers on retry.

### Tool interface

```python
from automation import RecruitmentBrowser

with RecruitmentBrowser(base_url="http://127.0.0.1:5000") as browser:
    jobs = browser.open_jobs()                      # -> ActionResult[list[JobSummary]]
    job = browser.select_job(title="AI Engineer")   # -> ActionResult[JobSummary]
    candidates = browser.filter_candidates(job_id=job.data.id, status="shortlisted")
    candidate = browser.get_candidate(candidate_id=candidates.data[0].id)
    interview = browser.create_interview(candidate_id=candidate.data.id,
                                          round_name="Technical Screen",
                                          scheduled_at="2026-10-10T10:00",
                                          interviewer="Priya Nair")
    status = browser.update_candidate_status(candidate_id=candidate.data.id, status="interview")
    verification = browser.verify_interview(candidate_id=candidate.data.id,
                                            round_name="Technical Screen")
```

Every action returns an `ActionResult` with `success`, typed `data`, a
structured `error` (type, message, page URL, details), a `screenshot` path,
and `duration_ms`. Screenshots are written to `screenshots/`. No business
logic lives inside the Playwright functions — they only interact with the UI
and return data, so a future agent can decide what to do with the results.

## Demo recruitment app (Phase 2)
A separate synthetic portal ("TalentDesk") that OpsPilot will operate via the browser. Own SQLite DB at `demo-app/data/recruit.db`, seeded on first run.

```bash
.venv/bin/pip install -r demo-app/requirements.txt
.venv/bin/uvicorn main:app --app-dir demo-app --port 5001    # http://localhost:5001
.venv/bin/python -m pytest demo-app/tests -q
```
Pages: `/` jobs, `/jobs/{id}?status=` candidates, `/candidates/{id}` details/status/scheduling, `/interviews` calendar, `/admin` failure toggle + reset, `/api/state` JSON snapshot (for verification code).
Failure simulation: set `SIMULATE_CALENDAR_FAILURE=true` (applies on seed/reset) or use `/admin`. One-shot by default; mode `before_write` or `after_write`. All UI elements carry `data-testid`.

## Phase 5 — Reliable Failure Recovery

The agent (`backend/app/agent/`) takes a plain-English goal such as

> Schedule a Technical Screen for Aarav Sharma with Priya Nair on 2026-10-08 at 10:00

and runs it in a real browser through the Phase 3 layer: it schedules the interview,
then moves the candidate to `interview` status, and verifies both.

**Recovery rule:** a failed create is never retried blindly. The agent re-opens the
candidate page to see what actually happened:

| What the state check finds | Decision |
|---|---|
| The interview exists (the failed request saved it) | Don't retry. Verify, then continue |
| It doesn't exist, and the error is transient (500/timeout) | Retry (bounded, with backoff), then verify |
| It doesn't exist, and the error is permanent (validation) | Stop with a blocker |
| The page can't be read | Stop: the outcome is unknown, and a retry could duplicate |
| The same round is already booked with different details | Stop: needs approval |

The same check runs **before** the first attempt, so re-running a finished goal creates nothing.
Verification reads `/api/state` (independent of the browser) and requires exactly one
matching interview.

Every step is a structured event (`GOAL_RECEIVED`, `PLAN`, `SETUP`, `STATE_CHECK`,
`ACTION`, `ACTION_SUCCEEDED`, `ACTION_FAILED`, `RECOVERY_STARTED`, `RETRY`, `SKIPPED`,
`VERIFICATION`, `COMPLETED`, `BLOCKED`). Events appear on the dashboard timeline, are
stored with the run in SQLite, and are logged as one JSON line each (logger `opspilot.agent`).

### Reproducible failure scenarios (for the demo video)

Pick one in the dashboard ("Failure scenario"). Before the run, the harness arms the
demo app's failure plan (`POST /admin/failure`; you can also set `DEMO_FAILING_ATTEMPTS` /
`DEMO_FAILURE_MODE` before starting the demo app):

| Scenario | Failure plan | Expected outcome |
|---|---|---|
| `none` | — | Completes in one attempt |
| `transient` | 1 × `before_write` | Fails → state check finds nothing → retry → completes |
| `saved_but_failed` | 1 × `after_write` | Fails, but the row was saved → state check finds it → **no retry, no duplicate** → completes |
| `outage` | 10 × `before_write` | 3 attempts, each followed by a state check → **BLOCKED**, with "nothing was created, safe to re-run" |

Keep "Reset demo data first" ticked so every take starts from the same seed data.

### Run it

macOS AirPlay Receiver holds port 5000, so the demo app runs on 5050 here
(set `DEMO_APP_URL` if you use another port).

```bash
# Terminal 1 — demo app
cd demo-app && ../.venv/bin/uvicorn app.main:app --port 5050

# Terminal 2 — backend (set AGENT_HEADLESS=false in .env to watch Chromium drive the app)
cd backend && ../.venv/bin/uvicorn app.main:app --port 8000

# Terminal 3 — dashboard at http://localhost:3000
cd frontend && npm run dev
```

API: `GET /api/scenarios`, `POST /api/runs {goal, scenario, reset_demo_data}`,
`GET /api/runs/{id}` (live events while running), `GET /api/runs`.

### Tests

```bash
cd backend && ../.venv/bin/python -m pytest -q   # boots the demo app + headless Chromium
```
