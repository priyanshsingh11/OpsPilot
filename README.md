# OpsPilot

**An operator agent for recruitment ops.** You give it a plain-English goal. It operates a web
application in a real Chromium browser, recovers from failures without duplicating work, **asks
for your approval before anything irreversible**, and then **proves** what it did by checking the
application's own records and showing screenshots.

Built for the Hulchul AI Engineering Internship build assignment.

> **Scope, honestly.** OpsPilot does one workflow (interview scheduling) in one synthetic
> application that ships with this repo. Goal understanding is **rule-based, not an LLM**, and the
> agent is a plain Python pipeline: **no LangGraph, no model calls, no Tailwind**. Nothing leaves
> your machine and no API key is needed. See [Limitations](#limitations).

## At a glance

| Assignment requirement | How OpsPilot meets it | Where to look |
|---|---|---|
| Working execution | Real Playwright actions change the demo app's records | [Demo workflow](#demo-workflow) |
| Adaptability | The same code handles a different job, duration and day, with no code change | [Running the agent](#running-the-agent) |
| Recovery | After a failure it re-reads the app before deciding; never blindly retries | [Failure simulation](#failure-simulation-and-recovery) |
| Verified completion | An independent before/after check decides COMPLETED / PARTIALLY_COMPLETED / FAILED | [Verification](#verification) |
| Human control | Approval before every email; pause, continue, stop, resume | [Human approval](#human-approval-and-control) |
| Evidence | Screenshots, event timeline, action ledger, verification report | [Evidence](#evidence) |

## Contents

1. [Quick start](#quick-start)
2. [The problem](#the-problem)
3. [Demo workflow](#demo-workflow)
4. [Architecture](#architecture)
5. [Tech stack](#tech-stack)
6. [Project structure](#project-structure)
7. [Setup](#setup)
8. [Environment variables](#environment-variables)
9. [Running the application](#running-the-application)
10. [Running the agent](#running-the-agent)
11. [Failure simulation and recovery](#failure-simulation-and-recovery)
12. [Human approval and control](#human-approval-and-control)
13. [Verification](#verification)
14. [Evidence](#evidence)
15. [Running tests](#running-tests)
16. [Demo instructions](#demo-instructions)
17. [Limitations](#limitations)
18. [Future work](#future-work)
19. [AI assistance disclosure](#ai-assistance-disclosure)
20. [Author contribution](#author-contribution)

Also: **[DEMO.md](DEMO.md)** (recording script), **[docs/ENGINEERING_NOTE.md](docs/ENGINEERING_NOTE.md)**
(design decisions, short) and **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** (run lifecycle in detail).

---

## Quick start

Requires **Python 3.11+** (tested on 3.12 and 3.14) and **Node 20+**. From the repo root:

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.local
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
(cd frontend && npm install)
```

Then, in three terminals:

```bash
cd demo-app && ../.venv/bin/uvicorn app.main:app --port 5050   # 1. the recruitment app
cd backend  && ../.venv/bin/uvicorn app.main:app --port 8000   # 2. API + agent
cd frontend && npm run dev                                     # 3. dashboard
```

Open **http://localhost:3000**, keep *Reset demo data first* ticked, and press **Run agent** with the
pre-filled goal. When the status turns **WAITING_FOR_APPROVAL**, review the email and approve it.

## The problem

Recruiting coordinators spend hours on clicks like *"book interviews for everyone we shortlisted
for the AI Engineer role, tomorrow afternoon, and send them invites"*. Scripting that is easy until
something goes wrong: the calendar times out, but did the booking go through? Retry blindly and
the candidate gets two interviews and two emails.

OpsPilot shows how an operator can do this kind of task **reliably**:

- it reads the real state of the app before and after every action, so it never repeats work;
- it stops for a human before sending an email to a candidate;
- it never reports success it has not verified, and says exactly what is left when it cannot finish.

## Demo workflow

Goal typed into the dashboard:

> Schedule interviews for all shortlisted AI Engineer candidates tomorrow afternoon.

What OpsPilot does, all through the browser:

1. Opens the AI Engineer job, filters candidates to **shortlisted**, finds Aarav Sharma and Diya Patel.
2. Reads the interview calendar: the hiring manager (Priya Nair) is already busy 14:00–15:00, so it
   plans 13:00 and 15:00.
3. For each candidate: checks they don't already have this interview, fills and submits the
   *Schedule interview* form, confirms the record, sets the status to `interview`.
4. Shows each invitation email (to, subject, message) and **waits for your approval** before sending it.
5. Runs an independent verification against the app's records and captures a final screenshot.

Result: **COMPLETED** with 7 passing checks. The same code handles
*"Schedule 45-minute interviews for shortlisted Full Stack Engineer candidates on Friday."*
(different role, duration, day and hiring manager).

## Architecture

```
 Dashboard (Next.js, :3000)        goal · live status · timeline · approval · verification · evidence
        │  HTTP/JSON (polling every 0.5 s)
        ▼
 Backend API (FastAPI, :8000)      runs · pause/continue/stop/resume · approvals · /screenshots
        │
        ├── Agent (backend/app/agent)          parse goal → plan → act with recovery → ask approval
        │       │                              (one background thread + one Chromium per run)
        │       ▼
        │   Automation layer (automation/)     Playwright actions → typed ActionResult
        │       │
        │       ▼
        │   Demo recruitment app (:5050)       jobs · candidates · interviews · invitations
        │                                      + deterministic failure injection
        │
        ├── Verification (backend/app/verification.py)   before vs after records → final status
        └── SQLite (backend/opspilot.db)       runs, event log, checkpoints, approvals
```

Three separations keep it honest:

- **The agent acts only through the browser.** Every read and write the goal needs goes through
  Playwright on the app's real pages.
- **Verification is separate from execution.** It ignores what the agent claims and compares the
  app's records before and after the run (via the demo app's read-only `/api/state`).
- **The demo harness is separate too.** Resetting data and arming failures use admin endpoints and
  appear as `SETUP` events, never as agent actions.

**A run, in order:** goal received → parse (names read from the app) → discover (filter candidates,
read the calendar) → plan (free slots around existing bookings) → per candidate: schedule + set status,
each with a state check and confirmation → per candidate: approval → send → verify → final
verification → terminal status. Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Event types** (the timeline): `GOAL_RECEIVED`, `SETUP`, `PLAN`, `STATE_CHECK`, `ACTION`,
`ACTION_SUCCEEDED`, `ACTION_FAILED`, `RECOVERY_STARTED`, `RETRY`, `SKIPPED`, `VERIFICATION`,
`APPROVAL_REQUESTED`, `APPROVAL_GRANTED`, `PAUSED`, `RESUMED`, `RUN_RESTARTED`, `EVIDENCE`,
`TARGET_DONE`, `TARGET_BLOCKED`, and the terminal `COMPLETED`, `PARTIALLY_COMPLETED`, `FAILED`,
`BLOCKED`, `REJECTED`, `STOPPED`. Each is also logged as one JSON line by the backend (`opspilot.agent`).

## Tech stack

| Part | Technology |
|---|---|
| Dashboard | Next.js 16, React 19, TypeScript, CSS modules |
| Backend API | Python, FastAPI, Pydantic Settings, SQLAlchemy 2 + SQLite |
| Agent | Plain Python (rule-based goal parser, planner, recovery and verification) |
| Browser automation | Playwright, Chromium, sync API |
| Demo application | FastAPI + Jinja2 server-rendered pages, in-memory data |
| Tests | pytest (real browser, real servers); ESLint and `tsc` for the frontend |

No external services, API keys or accounts are required.

## Project structure

```
backend/
  app/main.py            API: runs, controls, approvals, /screenshots
  app/verification.py    independent before/after verification → final status
  app/agent/
    runner.py            goal → plan → act → verify; batch and single-candidate flows
    goal.py              rule-based goal parser (batch and single)
    planning.py          free-slot finding around existing bookings
    scheduling.py        interview creation with state-aware recovery
    invitation.py        approval-gated invitation email
    control.py           pause / stop / approval gate (ApprovalGrant)
    manager.py, store.py run lifecycle and persistence (runs, checkpoints, approvals)
    driver.py            BrowserDriver (Playwright) + HarnessClient (admin, /api/state)
    evidence.py          action ledger
    events.py            structured event log
    scenarios.py         reproducible failure scenarios
  tests/                 integration and unit suites
automation/              Playwright layer: RecruitmentBrowser → ActionResult (+ tests)
demo-app/app/            the synthetic recruitment app the agent operates (+ tests in demo-app/tests)
frontend/                Next.js dashboard (app/page.tsx, app/panels.tsx, lib/)
docs/                    ARCHITECTURE.md, ENGINEERING_NOTE.md
DEMO.md                  5-minute recording script
screenshots/             evidence written at run time (git-ignored)
```

## Setup

Run from the repo root. This is the same as the [Quick start](#quick-start):

```bash
cp .env.example .env                        # backend settings (no secrets in it)
cp frontend/.env.example frontend/.env.local
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # backend, demo app, Playwright layer, pytest
.venv/bin/playwright install chromium       # one-time browser download
cd frontend && npm install
```

## Environment variables

**Backend** (`.env` at the repo root, read at startup; all optional):

| Variable | Default | Purpose |
|---|---|---|
| `DEMO_APP_URL` | `http://127.0.0.1:5050` | The demo app the agent operates |
| `AGENT_HEADLESS` | `true` | `false` opens a visible Chromium window (use it for the video) |
| `AGENT_MAX_ATTEMPTS` | `3` | Attempts for a failing action (first try plus retries) |
| `AGENT_RETRY_BACKOFF_SECONDS` | `1.0` | Wait between attempts (grows linearly) |
| `AGENT_STEP_DELAY_SECONDS` | `0.3` | Pause after each logged step, so a run is easy to follow |
| `DATABASE_URL` | `backend/opspilot.db` | SQLite file for runs, events and approvals |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Origins allowed to call the API |
| `ENVIRONMENT`, `BACKEND_HOST`, `BACKEND_PORT` | `development`, `127.0.0.1`, `8000` | Informational |

**Frontend** (`frontend/.env.local`): `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).

**Demo app** (shell variables, **not** `.env`): `DEMO_FAILING_ATTEMPTS` and `DEMO_FAILURE_MODE`, see
[Failure simulation](#failure-simulation-and-recovery).

## Running the application

Three processes, each in its own terminal, from the repo root. Start the demo app first.

```bash
cd demo-app && ../.venv/bin/uvicorn app.main:app --port 5050   # http://127.0.0.1:5050/jobs
cd backend  && ../.venv/bin/uvicorn app.main:app --port 8000   # http://127.0.0.1:8000/docs
cd frontend && npm run dev                                     # http://localhost:3000
```

Check it is up: `curl http://127.0.0.1:8000/api/health` returns `"status":"ok"`, and the dashboard
header shows **Backend ok**. (Port 5050 is used because macOS AirPlay Receiver holds 5000.)

**Start over:** tick *Reset demo data first* in the dashboard, or restart the demo app. The demo
app keeps its data in memory and re-seeds on start: 6 jobs (5 open), 12 synthetic candidates and
1 existing interview on the next business day at 14:00. Browse it at http://127.0.0.1:5050/jobs.

To **watch the browser** work, set `AGENT_HEADLESS=false` in `.env` and restart the backend.

## Running the agent

In the dashboard, type a goal, pick a failure scenario (or *Normal run*), keep *Reset demo data
first* ticked for repeatable results, and press **Run agent**. Candidate, job and interviewer names
are read from the app at run time, so none are hard-coded.

| Kind | Example goal |
|---|---|
| Batch | `Schedule interviews for all shortlisted AI Engineer candidates tomorrow afternoon.` |
| Batch, variation | `Schedule 45-minute interviews for shortlisted Full Stack Engineer candidates on Friday.` |
| Batch, explicit | `Book technical screens for shortlisted Backend Engineer candidates with Rahul Verma on 2026-10-09 starting at 2pm` |
| Single | `Schedule a Technical Screen for Aarav Sharma with Priya Nair on 2026-10-08 at 10:00` |
| No emails | add `without sending an invitation` to any goal |

**What the parser understands:** a job title, a pipeline status (`shortlisted`, `applied`, …), a round
name, an interviewer (default: the job's hiring manager), a date (`YYYY-MM-DD`, `October 9`,
`tomorrow`, a weekday; default: the next business day; a weekend `today`/`tomorrow` moves to Monday),
a time or part of day (`at 3pm`, `starting at 2pm`, `morning`, `afternoon`; default 10:00–18:00) and a
duration (`45-minute`, `an hour`; default 60). Every default it applies is listed in the plan. A goal it
cannot understand is **refused with the reason, and nothing is changed**.

**Over HTTP:** `POST /api/runs {"goal": "...", "scenario": "none", "reset_demo_data": true}`, then poll
`GET /api/runs/{id}`. Controls: `POST /api/runs/{id}/pause | continue | stop | resume` and
`POST /api/runs/{id}/approvals/{approval_id}/approve | reject`. All endpoints: http://127.0.0.1:8000/docs.

## Failure simulation and recovery

The demo app can make interview creation fail **on purpose and deterministically** (count-based, never
random). The dashboard's **Failure scenario** picker arms it right before the run, so every take behaves
the same:

| Scenario | What the app does | What OpsPilot does | Final status |
|---|---|---|---|
| Normal run | nothing | n/a | COMPLETED |
| Calendar fails once | 1st create fails, **nothing saved** | checks the page → not there → retries once | COMPLETED |
| Saved, but reported as failed | 1st create **is saved** but returns HTTP 500 | checks the page → it exists → **does not retry** | COMPLETED |
| Outage for one candidate | next 3 creates fail | 1st candidate gives up after 3 attempts; the others go through | PARTIALLY_COMPLETED |
| Calendar outage | next 10 creates fail | every candidate gives up after 3 attempts | FAILED |

**Reproduce the recovery demo:** goal *"Schedule interviews for all shortlisted AI Engineer candidates
tomorrow afternoon."*, scenario **Saved, but reported as failed**, *Reset* ticked, **Run agent**. The
timeline shows `ACTION_FAILED` → `STATE_CHECK [post-failure] … already exists` → `RECOVERY_STARTED … NOT
retrying`, then the run continues; the **Failure and recovery** panel shows the decision, and
verification shows *No duplicate interviews or invitations: PASS*.

**The recovery rule:** after any failed action the agent re-reads the app before deciding.

| After a failure, the page shows… | Decision |
|---|---|
| the interview exists | don't retry; treat as done and verify |
| nothing, and the error is transient (500, timeout) | retry, up to `AGENT_MAX_ATTEMPTS` in total, with backoff |
| nothing, and the error is permanent (validation) | stop for this candidate, with the reason |
| the page can't be read | stop: the outcome is unknown and a retry could duplicate |

The same state check runs **before** the first attempt, so re-running a finished goal creates nothing.
In a batch, one candidate's failure is recorded and the others continue.

**Other ways to arm failures:** `POST /admin/failure` on the demo app (`remaining`,
`mode=before_write|after_write`); the chaos-mode toggle on its `/interviews` page (every create fails
until switched off); or shell variables when starting the demo app:

```bash
DEMO_FAILING_ATTEMPTS=1 DEMO_FAILURE_MODE=after_write ../.venv/bin/uvicorn app.main:app --port 5050
```

## Human approval and control

Scheduling an interview and changing a status are routine and reversible, so the agent does them on its
own. **Sending an invitation email cannot be undone**, so every send needs your approval:

1. At the send step the run switches to **WAITING_FOR_APPROVAL**. The Approval panel shows who is
   affected, the exact subject and message, and what approve and reject will each do.
2. **Approve and send**: the agent sends exactly that email, then confirms the app recorded one invitation
   with that content.
3. **Reject**: nothing is sent; the run continues with the others and ends **PARTIALLY_COMPLETED**, with
   the unsent invitation under *What remains*. (Rejecting every email also ends PARTIALLY_COMPLETED: the
   interviews and statuses are real, only the emails were withheld.)

**Enforced in code, not only in the UI.** The only way to send is `BrowserDriver.send_invitation(grant, payload)`.
A grant exists only after the approval is read back from SQLite as `approved` for that exact email (by hash),
and it is re-checked at the moment of sending, so a stop, a reject, or a changed email invalidates it. A run
with no approval channel cannot send at all.

**Other controls:** **Pause** (takes effect at the next step boundary; an action already running in the
browser finishes first), **Continue**, **Stop** (ends the run and reports what was done), and **Resume**
for a run interrupted by a backend restart. Events, a checkpoint and approvals are saved as they happen;
resuming re-checks the app's state at every step, so finished work is not repeated and an existing
approval is reused.

## Verification

`backend/app/verification.py` runs at the end of every batch run and is deliberately independent of the
agent. It takes the app's records **before** and **after** the run and compares them with what the goal
asked for:

| Check | Passes when |
|---|---|
| Interview records exist | every target has exactly one matching interview (round, time, duration, interviewer) |
| Candidate statuses updated | every target has status `interview` |
| Invitations sent | every approved invitation is recorded as sent |
| No duplicate interviews or invitations | no candidate has the same round twice or two invitations for one interview |
| All required fields present | round, time, duration, interviewer and job are set on every new interview |
| No interviewer double-booked | no overlapping interviews for the interviewers involved |
| No unintended changes | no other candidate's status, interview or invitation changed |

The final status comes from these checks, not from the agent:

- **COMPLETED**: every candidate is fully done and every check passes.
- **PARTIALLY_COMPLETED**: some work is done (including interviews scheduled but invitations withheld), or all
  candidates are done but a safety check failed.
- **FAILED**: nothing the goal asked for was achieved.

Anything incomplete is listed under **What remains** with the reason. Re-running is always safe. Single-candidate
goals use the same per-step confirmations but not the before/after verifier.

## Evidence

Everything shown as evidence comes from real actions:

- **Screenshots**: Playwright saves one after every browser action (`-ok` or `-fail` in the file name), and
  the verifier captures the final interview calendar. Files are in `screenshots/`, served read-only by the
  backend at `/screenshots/{file}`, and shown in the dashboard's **Evidence** panel.
- **Timeline**: the full structured event log, stored with the run in SQLite.
- **Action ledger**: per action, its input, result, error, recovery link and screenshots, in `details.actions`
  of `GET /api/runs/{id}`.
- **Verification report**: the 7 checks above, per run.

## Running tests

All tests start their own demo-app server on port 5065 and drive headless Chromium. Nothing is mocked
except where noted. **Run one pytest session at a time** (they share that port).

```bash
cd backend && ../.venv/bin/python -m pytest -q             # 49 tests: agent, recovery, approval, end-to-end
cd .. && .venv/bin/python -m pytest -q automation/tests    # 4 tests: the Playwright layer
.venv/bin/python -m pytest -q demo-app/tests               # 9 tests: the demo app on its own (fast, no browser)
cd frontend && npx tsc --noEmit && npm run lint            # frontend type check and lint
```

What the end-to-end suite (`backend/tests/test_end_to_end.py`) proves, through the real API and judged by
the demo app's own records:

1. **Normal run**: candidates found in the UI, interviews around an existing booking, statuses updated, two
   approvals that each resume the run, all checks pass, evidence screenshot exists.
2. **Variation**: Full Stack Engineer, 45-minute slots back to back on Friday, a different hiring manager.
3. **Failure**: saved-but-failed is detected by a state check and not retried; a transient failure is
   retried once.
4. **Pause**: nothing changes while paused; continuing finishes with no duplicates.
5. **Unrecoverable failure**: PARTIALLY_COMPLETED or FAILED, never COMPLETED, with the reason per candidate.
6. **Reject**: one rejection and all rejections both end PARTIALLY_COMPLETED with nothing sent.

Plus re-running a finished goal (does nothing), the approval gate (a driver call without a valid grant
sends nothing), stop, crash-and-resume, and goal parsing. `test_recovery_unit.py` uses in-memory fakes for the
cases the demo app can't produce (an unreadable page after a failure, a permanent error).

## Demo instructions

[DEMO.md](DEMO.md) is a timed script for a 5-minute recording: normal run with approval, verification and
evidence, the Full Stack variation, failure with recovery, and an honest partial failure. It lists the exact
goals to type, the scenario to pick for each, and what to point out on screen.

## Limitations

- **No LLM and no LangGraph.** The agent is a plain Python pipeline (parse → plan → act → verify) with an
  explicit event log and checkpoint. The dashboard uses CSS modules, not Tailwind.
- **Rule-based goal understanding.** Regular expressions, not a model. Phrasings outside the documented
  patterns are refused with a reason rather than guessed.
- **One workflow, one app.** Only interview scheduling (plus status update and invitation) in the bundled
  synthetic app.
- **Simple scheduling model.** Only the interviewer's calendar is considered: no candidate availability,
  time zones or holidays. A weekend `today`/`tomorrow` moves to Monday; an explicit date or weekday is taken
  as written.
- **Emails are not really sent.** The demo app records an invitation as "sent"; nothing leaves the machine.
- **One run at a time**, and one approval per email (no bulk approve).
- **Pause is between steps**, not mid-action: an action already in the browser finishes first.
- **The before/after verifier covers batch goals only.** Single-candidate goals rely on per-step confirmations.
- **Verification reads a demo-only endpoint** (`/api/state`). A real system would use the product's read API
  or re-read the UI.
- **No authentication** on the dashboard, the API, or the demo app's `/admin/*` and `/api/state` endpoints
  (the harness uses them to reset data and arm failures). Everything is meant for localhost only.
- **The demo app's data is in memory:** restarting it resets everything. "Tomorrow" is relative to the day
  you run it.
- **The dashboard polls** every 0.5 s instead of streaming.
- **Screenshots are never cleaned up** (the `screenshots/` folder only grows).

## Future work

- An LLM planner behind the same plan format (with the rule-based parser as a fallback), so more phrasings
  and workflows work.
- Run the before/after verifier for single-candidate goals too, and show the action ledger as its own table.
- Candidate availability and time zones in slot finding; reschedule and cancel flows (with approval).
- One approval for a batch of emails, with per-email opt-out.
- Server-sent events instead of polling; authentication and an audit trail of who approved what.
- Point the same driver interface at a second, real-world app (a calendar or ATS sandbox).
- A screenshot retention policy and a per-run evidence bundle download.

## AI assistance disclosure

- **Claude Code (Anthropic)** was used heavily, across several parallel sessions, to generate and revise most
  of the code, tests and documentation in this repository, phase by phase (foundation, demo app, Playwright
  layer, failure recovery, human control, verification, end-to-end testing, audit). Its output was directed
  by written phase specifications and reviewed by the author.
- **Libraries:** FastAPI, Pydantic, SQLAlchemy, Jinja2, Playwright, pytest, Next.js and React. No code was
  copied from other projects.
- **No AI at run time.** OpsPilot itself makes no model calls; its behaviour is deterministic.
- All data is synthetic. No credentials are in the repository (`.env` is git-ignored; `.env.example` has none).

## Author contribution

> ✏️ **To be completed by Priyansh before submitting.** Describe in your own words what you designed,
> decided, wrote, reviewed and debugged yourself: for example the choice of the recruiting workflow and the
> synthetic app, the phase plan, the recovery and approval rules, what you changed after reviewing generated
> code, and what you tested by hand. This section is left open on purpose so it states only what is true.
