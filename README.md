# OpsPilot

A computer-operator prototype: it receives a plain-English goal, operates
applications on a real browser, and finishes the work with evidence.

OpsPilot is built as a Hulchul AI Engineering internship assignment. The idea is
a system that takes a goal such as *"Schedule a Technical Screen for Aarav
Sharma with Priya Nair on 2026-10-08 at 10:00"*, drives a synthetic
recruitment portal in Chromium, and proves the work was done — recovering
cleanly when the application fails along the way.

---

## Table of contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Project layout](#project-layout)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Running the system](#running-the-system)
- [Components](#components)
  - [Frontend — dashboard (port 3000)](#frontend--dashboard-port-3000)
  - [Backend — API (port 8000)](#backend--api-port-8000)
  - [Demo app — synthetic recruitment portal](#demo-app--synthetic-recruitment-portal)
  - [Automation layer — Playwright](#automation-layer--playwright)
  - [Agent — goal runner (Phase 5)](#agent--goal-runner-phase-5)
- [API reference](#api-reference)
- [Testing](#testing)
- [Configuration](#configuration)
- [Demo failure scenarios](#demo-failure-scenarios)
- [Design notes](#design-notes)

---

## Overview

OpsPilot is organised as a set of small, testable layers:

1. **A synthetic recruitment portal** (`demo-app/`) — a realistic web app with
   jobs, candidates, interviews, and a controllable failure switch. This is the
   "computer" the operator works on.
2. **A browser automation layer** (`automation/`) — a Playwright tool
   interface that opens the portal, performs actions, and returns structured
   results. No business logic lives here.
3. **An agent** (`backend/app/agent/`) — takes a plain-English goal, plans the
   browser actions, and runs them with reliable failure recovery.
4. **A dashboard** (`frontend/`) — shows live run progress, events, evidence
   screenshots, and human controls (pause / continue / stop / approve).

The system is deliberately deterministic: the demo app seeds fixed data, every
UI element carries a `data-testid`, and failures can be armed reproducibly, so
the whole workflow can be tested end-to-end without any external services.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         Dashboard (frontend)                       │
│   goal input · live events · evidence screenshots · controls      │
└───────────────────────────────┬──────────────────────────────────┘
                                │ HTTP (JSON)
┌───────────────────────────────▼──────────────────────────────────┐
│                     Backend (FastAPI, port 8000)                   │
│   /api/runs · /api/scenarios · /api/health · /screenshots          │
│                                                                    │
│   ┌────────────────────────────────────────────────────────────┐  │
│   │ Agent (backend/app/agent/)                                  │  │
│   │  goal → plan → act → verify, with failure recovery           │  │
│   └───────────────────────────┬────────────────────────────────┘  │
└───────────────────────────────┼──────────────────────────────────┘
                                │ Playwright (Chromium)
┌───────────────────────────────▼──────────────────────────────────┐
│              Automation layer (automation/)                        │
│   open_jobs · filter_candidates · create_interview · verify …     │
│   every action → ActionResult{ success, data, error, screenshot } │
└───────────────────────────────┬──────────────────────────────────┘
                                │ drives
┌───────────────────────────────▼──────────────────────────────────┐
│           Demo app — synthetic recruitment portal                  │
│   jobs · candidates · interviews · status updates                 │
│   failure switch: chaos mode / before_write / after_write         │
└──────────────────────────────────────────────────────────────────┘
```

---

## Project layout

```
.
├── backend/                     # FastAPI backend + agent (port 8000)
│   ├── app/
│   │   ├── main.py              # API routes, lifespan, screenshot mount
│   │   ├── config.py            # Settings (env-driven)
│   │   ├── database.py          # SQLite engine, init/health
│   │   ├── models.py            # SQLAlchemy tables (runs, events, approvals)
│   │   └── agent/               # Phase 5 goal runner
│   │       ├── manager.py       # Run lifecycle, pause/continue/stop/resume
│   │       ├── runner.py        # goal → plan → act → verify loop
│   │       ├── driver.py        # BrowserDriver + HarnessClient (Playwright)
│   │       ├── planning.py      # goal → structured plan
│   │       ├── scheduling.py    # interview scheduling + recovery rule
│   │       ├── scenarios.py     # reproducible failure scenarios
│   │       ├── events.py        # structured run events (JSON log)
│   │       ├── control.py       # RunControl state machine
│   │       ├── evidence.py      # screenshot/evidence capture
│   │       ├── goal.py          # goal parsing/validation
│   │       ├── store.py         # RunStore (persistence)
│   │       └── invitation.py    # approval prompts
│   ├── tests/                   # backend + agent tests
│   └── requirements.txt
│
├── frontend/                    # Next.js dashboard (port 3000)
│   ├── app/                     # page.tsx, layout.tsx, globals.css
│   ├── lib/                     # api.ts (client), runState.ts
│   └── public/
│
├── demo-app/                    # Synthetic recruitment portal
│   ├── app/                     # in-memory variant (Phase 3 target)
│   │   ├── main.py              # routes: jobs, candidates, interviews, admin
│   │   ├── models.py            # Job/Candidate/Interview + seed data
│   │   └── store.py             # in-memory store + failure simulation
│   ├── main.py                  # SQLite-backed variant ("TalentDesk", port 5001)
│   ├── db.py                    # SQLite schema, seed, failure plan
│   ├── templates/               # Jinja2 templates (all carry data-testid)
│   ├── static/style.css
│   ├── tests/test_demo_app.py
│   └── alt-simple-app/          # minimal single-file variant
│
├── automation/                  # Playwright browser automation layer
│   ├── browser.py               # Chromium session + screenshots
│   ├── actions.py               # RecruitmentBrowser tool interface
│   ├── results.py               # ActionResult / ActionError / domain types
│   ├── errors.py                # exception → structured error
│   └── tests/
│       ├── conftest.py          # boots demo app + browser fixtures
│       └── test_workflow.py     # end-to-end workflow + failure tests
│
├── screenshots/                 # captured evidence (gitignored)
├── .env.example                 # backend + agent + demo config
└── README.md
```

---

## Prerequisites

- **Python 3.11+**
- **Node 20+**
- **Playwright Chromium** — installed once via:
  ```bash
  .venv/bin/playwright install chromium
  ```

---

## Setup

```bash
# 1. Environment files
cp .env.example .env
cp frontend/.env.example frontend/.env.local

# 2. Python dependencies (backend + demo app + automation)
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
.venv/bin/playwright install chromium

# 3. Frontend dependencies
cd frontend && npm install && cd ..
```

---

## Running the system

The system runs as three processes. From the repo root:

```bash
# Terminal 1 — demo app (synthetic recruitment portal)
#   in-memory variant on 5050 (used by the agent and Phase 5)
cd demo-app && ../.venv/bin/uvicorn app.main:app --port 5050

# Terminal 2 — backend API + agent (port 8000)
cd backend && ../.venv/bin/uvicorn app.main:app --port 8000

# Terminal 3 — dashboard (port 3000)
cd frontend && npm run dev
```

Then open **http://localhost:3000**, type a goal, and run it.

> **Note on ports:** macOS AirPlay Receiver holds port 5000, so the demo app
> defaults to **5050**. The SQLite-backed "TalentDesk" variant
> (`demo-app/main.py`) runs on **5001** if you prefer persistent data:
> ```bash
> .venv/bin/uvicorn main:app --app-dir demo-app --port 5001
> ```

To watch Chromium drive the app instead of running headless, set
`AGENT_HEADLESS=false` in `.env`.

---

## Components

### Frontend — dashboard (port 3000)

Next.js app that talks to the backend API. It provides:

- **Goal input** — a plain-English goal, plus a failure-scenario picker and a
  "reset demo data first" toggle.
- **Live execution status** — current step, run state.
- **Activity timeline** — structured events as they happen
  (`GOAL_RECEIVED`, `PLAN`, `ACTION`, `ACTION_FAILED`, `RECOVERY_STARTED`,
  `RETRY`, `VERIFICATION`, `COMPLETED`, `BLOCKED`, …).
- **Evidence** — screenshots the browser captured, served from `/screenshots`.
- **Human controls** — pause, continue, stop, resume, and approve/reject when
  the agent asks for approval.

### Backend — API (port 8000)

FastAPI app that owns run lifecycle and persistence. See
[API reference](#api-reference) for endpoints. Key behaviours:

- Runs execute in the background; their full event log is persisted to SQLite
  as it happens.
- A run interrupted by a process restart is marked **interrupted** and can be
  **resumed** — the agent re-checks the app's real state at every step, so
  resuming never repeats work.
- Only one run at a time (two runs would make each other's state checks
  meaningless).

### Demo app — synthetic recruitment portal

The controlled web app OpsPilot operates. Two variants exist:

| Variant | Entry point | Data | Port | Used by |
|---|---|---|---|---|
| In-memory | `demo-app/app/main.py` | re-seeded on start | 5050 | Phase 3 tests, Phase 5 agent |
| SQLite ("TalentDesk") | `demo-app/main.py` | persistent SQLite | 5001 | alternative / persistent |

Both expose the same pages and the same failure switches. All UI elements
carry `data-testid` attributes for stable automation.

**Pages:**

| Route | Purpose |
|---|---|
| `/jobs` | List all jobs |
| `/jobs/{id}?status=` | Job detail + candidates (filter by status / search) |
| `/candidates/{id}` | Candidate profile, status form, interviews |
| `/candidates/{id}/interviews/new` | Schedule-interview form |
| `/interviews` | Interview calendar + failure controls |
| `/admin` | Failure-plan toggle + reset |
| `/api/state` | Read-only JSON snapshot (for verification code) |

**Failure simulation** — the portal can be told to fail interview creation:

- **Chaos mode** — every create fails with a 500 until switched off.
- **Failure plan** — the next *N* creates fail, in one of two modes:
  - `before_write` — nothing is saved, a 500 is returned.
  - `after_write` — the interview **is saved**, but a 500 is still returned
    (like a calendar sync timing out after commit). A blind retry would
    create a duplicate.

The plan can be armed from the dashboard, via `POST /admin/failure`, or with the
`DEMO_FAILING_ATTEMPTS` / `DEMO_FAILURE_MODE` environment variables.

### Automation layer — Playwright

`automation/` is a thin, reliable tool interface over the demo app. It launches
Chromium, performs UI actions, and returns structured results. **It contains no
business logic** — it only interacts with the UI and returns data, so the agent
(or tests) decide what to do next.

```python
from automation import RecruitmentBrowser

with RecruitmentBrowser(base_url="http://127.0.0.1:5050") as browser:
    jobs       = browser.open_jobs()                        # list jobs
    job        = browser.select_job(title="AI Engineer")    # open a job
    candidates = browser.filter_candidates(job_id=job.data.id, status="shortlisted")
    candidate  = browser.get_candidate(candidate_id=candidates.data[0].id)
    interview  = browser.create_interview(
        candidate_id=candidate.data.id,
        round_name="Technical Screen",
        scheduled_at="2026-10-10T10:00",
        interviewer="Priya Nair",
    )
    status     = browser.update_candidate_status(candidate_id=candidate.data.id, status="interview")
    verified   = browser.verify_interview(candidate_id=candidate.data.id, round_name="Technical Screen")
```

**Every action returns the same shape:**

```python
ActionResult(
    action="create_interview",
    success=True,                    # or False
    data=InterviewDetails(...),      # typed domain data on success
    error=ActionError(               # structured failure info on failure
        type="server_error",         # timeout | not_found | server_error | validation | ...
        message="...",
        page_url="...",
        details={"status_code": "500"},
    ),
    screenshot="screenshots/….png",  # evidence captured around the action
    duration_ms=812.3,
)
```

Screenshots are written to `screenshots/` after every action, labelled with the
action name and outcome (`-ok` / `-fail`).

### Agent — goal runner (Phase 5)

`backend/app/agent/` takes a plain-English goal and runs it through the
automation layer with **reliable failure recovery**.

Given:

> Schedule a Technical Screen for Aarav Sharma with Priya Nair on 2026-10-08 at 10:00

the agent parses the goal into a plan, then executes: find the candidate →
schedule the interview → move the candidate to `interview` status → verify.

**Recovery rule:** a failed create is never retried blindly. The agent re-opens
the candidate page (or reads `/api/state`) to see what actually happened:

| What the state check finds | Decision |
|---|---|
| The interview exists (the failed request saved it) | Don't retry. Verify, then continue |
| It doesn't exist, error is transient (500/timeout) | Retry (bounded, with backoff), then verify |
| It doesn't exist, error is permanent (validation) | Stop with a concrete blocker |
| The page can't be read | Stop — outcome unknown, a retry could duplicate |
| The same round is already booked with different details | Stop — needs approval |

The same check runs **before** the first attempt, so re-running a finished goal
creates nothing. Verification reads `/api/state` (independent of the browser)
and requires exactly one matching interview.

---

## API reference

Base URL: `http://localhost:8000`

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Service + database health |
| `GET` | `/api/scenarios` | Available failure scenarios |
| `POST` | `/api/runs` | Start a run — `{goal, scenario?, reset_demo_data?}` (202) |
| `GET` | `/api/runs` | Recent runs |
| `GET` | `/api/runs/{id}` | Run detail + live events |
| `POST` | `/api/runs/{id}/pause` | Pause a running run |
| `POST` | `/api/runs/{id}/continue` | Continue a paused run |
| `POST` | `/api/runs/{id}/stop` | Stop a run |
| `POST` | `/api/runs/{id}/resume` | Resume an interrupted run (202) |
| `POST` | `/api/runs/{id}/approvals/{aid}/approve` | Approve a pending approval |
| `POST` | `/api/runs/{id}/approvals/{aid}/reject` | Reject a pending approval |
| `GET` | `/screenshots/{path}` | Evidence screenshots captured by the browser |

Interactive docs: **http://localhost:8000/docs**

---

## Testing

All tests are deterministic — they boot their own demo app instance and run
headless Chromium.

```bash
# Backend + agent tests (boots demo app + headless Chromium)
cd backend && ../.venv/bin/python -m pytest -q

# Automation layer workflow tests (boots its own demo app instance)
.venv/bin/python -m pytest automation/tests/ -v

# Demo app tests
.venv/bin/python -m pytest demo-app/tests -q
```

The automation tests prove the layer can deterministically:

1. Find shortlisted AI Engineer candidates,
2. Create an interview for one candidate,
3. Update the candidate status,
4. Verify the interview exists,

and that it detects a simulated outage, exposes structured error information,
and recovers on retry — including the `after_write` case where a blind retry
would duplicate the interview.

---

## Configuration

Configuration is env-driven. Copy `.env.example` to `.env` and adjust.

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | Environment label |
| `BACKEND_HOST` / `BACKEND_PORT` | `127.0.0.1` / `8000` | Backend bind address |
| `DATABASE_URL` | `sqlite:///./backend/opspilot.db` | Backend SQLite location |
| `CORS_ORIGINS` | `http://localhost:3000,…` | Allowed browser origins |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Frontend API base (in `frontend/.env.local`) |
| `DEMO_APP_URL` | `http://127.0.0.1:5050` | Demo app the agent operates |
| `AGENT_HEADLESS` | `true` | `false` to watch Chromium drive the app |
| `AGENT_MAX_ATTEMPTS` | `3` | Max create attempts before blocking |
| `AGENT_RETRY_BACKOFF_SECONDS` | `1.0` | Backoff between retries |
| `AGENT_STEP_DELAY_SECONDS` | `0.3` | Delay between browser steps |
| `DEMO_FAILING_ATTEMPTS` | `0` | Arm N failing interview creates on seed/reset |
| `DEMO_FAILURE_MODE` | `before_write` | `before_write` or `after_write` |

---

## Demo failure scenarios

Pick a scenario in the dashboard (or arm it via `POST /admin/failure` /
env vars). Each behaves the same way every time:

| Scenario | Failure plan | Expected outcome |
|---|---|---|
| `none` | — | Completes in one attempt |
| `transient` | 1 × `before_write` | Fails → state check finds nothing → retry → completes |
| `saved_but_failed` | 1 × `after_write` | Fails, but the row was saved → state check finds it → **no retry, no duplicate** → completes |
| `outage` | 10 × `before_write` | 3 attempts, each followed by a state check → **BLOCKED**, with "nothing was created, safe to re-run" |

Keep **"Reset demo data first"** ticked so every take starts from the same
seed data.

---

## Design notes

- **No business logic in the browser layer.** The Playwright functions only
  navigate, click, fill, and extract data. All decisions (what to do on
  failure, whether to retry, whether to ask the user) live in the agent.
- **Structured results over strings.** Every action returns a typed
  `ActionResult`; failures carry a machine-readable `type`, the page URL, and
  extra details, so a future agent can reason about them without parsing text.
- **Determinism first.** Fixed seed data, `data-testid` selectors, explicit
  waits, and reproducible failure plans make the whole workflow testable
  end-to-end without external services.
- **Evidence by default.** Screenshots are captured after every action and
  stored with the run, so the dashboard can show *what actually happened*, not
  just what the agent claims happened.
- **Human control.** Runs can be paused, continued, stopped, and resumed; the
  agent asks for approval when an action exceeds its authority.
