# OpsPilot

OpsPilot is a small **computer operator**. You give it a plain-English business goal; it
operates a web application in a real Chromium browser, recovers from failures without
duplicating work, asks for your approval before anything irreversible, and then **proves**
what it did by checking the application's records and showing screenshots.

Built for the Hulchul AI Engineering Internship build assignment.

> **Scope, honestly:** OpsPilot does one workflow (interview scheduling) in one synthetic
> application that ships with this repo. Goal understanding is rule-based, not an LLM.
> See [Known limitations](#known-limitations).

---

## Contents

- [The problem](#the-problem)
- [Example workflow](#example-workflow)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Install](#install)
- [Run](#run)
- [Using the agent](#using-the-agent)
- [Failure simulation and the recovery demo](#failure-simulation-and-the-recovery-demo)
- [How human approval works](#how-human-approval-works)
- [How verification works](#how-verification-works)
- [Testing](#testing)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Known limitations](#known-limitations)
- [What I personally built](#what-i-personally-built)
- [AI and tool assistance](#ai-and-tool-assistance)
- [Future improvements](#future-improvements)

Recording the demo video: see **[DEMO.md](DEMO.md)**. Deeper design notes:
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**; the short engineering note is **[docs/ENGINEERING_NOTE.md](docs/ENGINEERING_NOTE.md)**.

---

## The problem

Recruiting coordinators spend hours on clicks like *"book interviews for everyone we
shortlisted for the AI Engineer role, tomorrow afternoon, and send them invites"*. Automating
that with a script is easy until something goes wrong: the calendar times out, but did the
booking go through? Retry blindly and the candidate gets two interviews and two emails.

OpsPilot shows how an operator agent can do this kind of task **reliably**:

- it reads the real state of the app before and after every action, so it never repeats work,
- it stops for a human before sending an email to a candidate,
- it never reports success it hasn't verified, and says exactly what is left when it can't finish.

## Example workflow

Goal typed into the dashboard:

> Schedule interviews for all shortlisted AI Engineer candidates tomorrow afternoon.

What OpsPilot does, all through the browser:

1. Opens the AI Engineer job, filters candidates to **shortlisted**, and finds Aarav Sharma and Diya Patel.
2. Reads the interview calendar: the hiring manager (Priya Nair) is already busy 14:00–15:00,
   so it plans 13:00 and 15:00.
3. For each candidate: checks they don't already have this interview, fills and submits the
   *Schedule interview* form, confirms the record, moves the candidate to status `interview`.
4. Shows you each invitation email (to, subject, message) and **waits for your approval** before sending it.
5. Runs an independent verification against the app's records and captures a final screenshot.

Result: **COMPLETED**, with 7 passing checks (records exist, statuses updated, invitations
sent, no duplicates, required fields present, no double-booking, nothing outside the goal changed).

The same code handles a variation such as *"Schedule 45-minute interviews for shortlisted Full
Stack Engineer candidates on Friday."* (different role, duration, day and hiring manager).

## Architecture

```
 Dashboard (Next.js, :3000)        goal · live status · timeline · approval · verification · evidence
        │  HTTP/JSON (polling)
        ▼
 Backend API (FastAPI, :8000)      runs, pause/continue/stop/resume, approvals, /screenshots
        │
        ├── Agent (backend/app/agent)          parse goal → plan → act with recovery → ask approval
        │       │                              (one background thread + one Chromium per run)
        │       ▼
        │   Automation layer (automation/)     Playwright: open_jobs, filter_candidates,
        │       │                              create_interview, send_invitation, … → ActionResult
        │       ▼
        │   Demo recruitment app (demo-app/app, :5050)   jobs · candidates · interviews · invitations
        │                                                + deterministic failure injection
        │
        ├── Verification (backend/app/verification.py)   reads /api/state before vs after; decides
        │                                                COMPLETED / PARTIALLY_COMPLETED / FAILED
        └── SQLite (backend/opspilot.db)       runs, event log, checkpoints, approvals
```

Key separation:

- **The agent acts only through the browser.** Every read and write the goal needs goes
  through Playwright on the app's real pages.
- **Verification is separate from execution.** It ignores what the agent claims; it compares the
  app's records before and after the run (via the demo app's read-only `/api/state`).
- **The demo harness is separate too.** Resetting data and arming failures use admin endpoints
  and are logged as `SETUP` events, never as agent actions.

More detail (run lifecycle, recovery rule, approval gate, data model): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tech stack

| Part | Technology |
|---|---|
| Dashboard | Next.js 16, React 19, TypeScript, CSS modules |
| Backend API | Python 3.11+, FastAPI, Pydantic Settings, SQLAlchemy 2 + SQLite |
| Browser automation | Playwright (Chromium, sync API) |
| Demo application | FastAPI + Jinja2 server-rendered pages, in-memory data |
| Tests | pytest (real browser, real servers), ESLint + `tsc` for the frontend |

No external services, API keys or accounts are required.

## Install

Requires **Python 3.11+** (tested with 3.12 and 3.14) and **Node 20+**. Commands are run from the repo root.

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.local

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium

cd frontend && npm install && cd ..
```

## Run

Three processes, each in its own terminal, from the repo root:

```bash
# 1. The synthetic recruitment app (http://127.0.0.1:5050)
cd demo-app && ../.venv/bin/uvicorn app.main:app --port 5050
```

```bash
# 2. Backend API + agent (http://127.0.0.1:8000, API docs at /docs)
cd backend && ../.venv/bin/uvicorn app.main:app --port 8000
```

```bash
# 3. Dashboard (http://localhost:3000)
cd frontend && npm run dev
```

Check it is up: `curl http://127.0.0.1:8000/api/health` returns `"status":"ok"`, and the
dashboard header shows **Backend ok**.

To **watch the browser** while the agent works, set `AGENT_HEADLESS=false` in `.env` and
restart the backend.

The demo app keeps its data in memory: restarting it, or ticking **Reset demo data first**
in the dashboard, restores the seed data (6 jobs, 5 of them open; 12 candidates; 1 existing interview on the next business day).
You can browse it directly at http://127.0.0.1:5050/jobs.

## Using the agent

Open http://localhost:3000, type a goal, pick a failure scenario (or *Normal run*), keep
**Reset demo data first** ticked for repeatable results, and press **Run agent**.

Goals it understands (the candidate, job and interviewer names are read from the app at run time):

| Kind | Example |
|---|---|
| Batch | `Schedule interviews for all shortlisted AI Engineer candidates tomorrow afternoon.` |
| Batch, variation | `Schedule 45-minute interviews for shortlisted Full Stack Engineer candidates on Friday.` |
| Batch, explicit | `Book technical screens for shortlisted Backend Engineer candidates with Rahul Verma on 2026-10-09 starting at 2pm` |
| Single | `Schedule a Technical Screen for Aarav Sharma with Priya Nair on 2026-10-08 at 10:00` |
| Opt out of emails | add `without sending an invitation` |

What is understood: a job title, a pipeline status (`shortlisted`, `applied`, …), a round name,
an interviewer (default: the job's hiring manager), a date (`YYYY-MM-DD`, `October 9`,
`tomorrow`, a weekday name; default: next business day; `today`/`tomorrow` falling on a weekend moves to Monday), a time or part of day (`at 3pm`,
`starting at 2pm`, `morning`, `afternoon`; default 10:00–18:00) and a duration (`45-minute`,
`an hour`; default 60). Every default it applies is listed in the plan. A goal it cannot
understand is refused with the reason, and nothing is changed.

You can also drive it over HTTP: `POST /api/runs {"goal": "...", "scenario": "none", "reset_demo_data": true}`,
then poll `GET /api/runs/{id}`. All endpoints are listed at http://127.0.0.1:8000/docs.

## Failure simulation and the recovery demo

The demo app can make interview creation fail on purpose. The dashboard's **Failure scenario**
picker arms it right before the run, so every take behaves the same:

| Scenario | What the app does | What OpsPilot does | Final status |
|---|---|---|---|
| Normal run | nothing | — | COMPLETED |
| Calendar fails once | 1st create fails, **nothing saved** | checks the page → not there → retries once | COMPLETED |
| Saved, but reported as failed | 1st create **is saved** but returns HTTP 500 | checks the page → it exists → **does not retry** (no duplicate) | COMPLETED |
| Outage for one candidate | next 3 creates fail | 1st candidate gives up after 3 attempts, others go through | PARTIALLY_COMPLETED |
| Calendar outage | next 10 creates fail | every candidate gives up after 3 attempts | FAILED |

**Reproducing the recovery demo:** goal *"Schedule interviews for all shortlisted AI Engineer
candidates tomorrow afternoon."*, scenario **Saved, but reported as failed**, Reset ticked,
Run. In the timeline you will see `ACTION_FAILED` → `STATE_CHECK [post-failure] … already
exists` → `RECOVERY_STARTED … NOT retrying`, then the run continues; the **Failure and
recovery** panel shows the decision and the final verification shows *No duplicate interviews
or invitations: PASS*. Step-by-step script: [DEMO.md](DEMO.md).

The recovery rule, in short: after any failed action the agent re-reads the app before deciding.

| After a failure, the page shows… | Decision |
|---|---|
| the interview exists | don't retry; treat as done and verify |
| nothing, and the error is transient (500, timeout) | retry, up to `AGENT_MAX_ATTEMPTS` in total, with backoff |
| nothing, and the error is permanent (validation) | stop for this candidate with the reason |
| the page can't be read | stop; the outcome is unknown and a retry could duplicate |

Other ways to arm failures (for experiments): `POST /admin/failure` on the demo app
(`remaining`, `mode=before_write|after_write`), the chaos-mode toggle on its `/interviews`
page, or shell variables when starting it:
`DEMO_FAILING_ATTEMPTS=1 DEMO_FAILURE_MODE=after_write ../.venv/bin/uvicorn app.main:app --port 5050`.

## How human approval works

Scheduling an interview and changing a status are routine and reversible in the app, so the
agent does them on its own. **Sending an invitation email to a candidate cannot be undone**, so
every send needs your approval:

1. When it reaches the send step, the run switches to **WAITING_FOR_APPROVAL**. The Approval panel
   shows the recipient, the exact subject and message, and what approve and reject will each do.
2. **Approve and send**: the agent sends exactly that email, then confirms the app recorded one invitation with that content.
3. **Reject**: nothing is sent; the run continues with the other candidates and ends
   **PARTIALLY_COMPLETED**, listing the unsent invitation under *What remains*.

The gate is enforced in code, not only in the UI: the only way to send is
`BrowserDriver.send_invitation(grant, payload)`, and a grant exists only after the approval is
read back from the database as `approved` for that exact email (its hash). It is re-checked at
the moment of sending, so a stop, a reject, or a changed email invalidates it.

Other controls on the dashboard: **Pause** (takes effect at the next step boundary; an action
already in the browser finishes first), **Continue**, **Stop** (ends the run and reports what
was done), and **Resume** for a run interrupted by a backend restart (it re-checks the app's
state, so finished work is not repeated). To skip invitations entirely, add *"without sending
an invitation"* to the goal.

## How verification works

`backend/app/verification.py` runs at the end of every batch run and is deliberately independent
of the agent: it takes the app's records **before** the run and **after** it and compares them
with what the goal asked for.

| Check | Passes when |
|---|---|
| Interview records exist | every target candidate has exactly one matching interview (round, time, duration, interviewer) |
| Candidate statuses updated | every target candidate has status `interview` |
| Invitations sent | every approved invitation is recorded as sent |
| No duplicate interviews or invitations | no candidate has the same round twice or two invitations for one interview |
| All required fields present | round, time, duration, interviewer and job are set on every new interview |
| No interviewer double-booked | no overlapping interviews for the interviewers involved |
| No unintended changes | no other candidate's status, interview or invitation changed |

The final status comes from these checks, not from the agent:

- **COMPLETED**: every candidate is fully done and every check passes.
- **PARTIALLY_COMPLETED**: some candidates are done (or all are, but a safety check failed).
- **FAILED**: none could be completed.

Anything incomplete is listed under **What remains** with the reason and whether it is safe to
re-run. Re-running is always safe: the agent finds what already exists and creates nothing twice.

Evidence: Playwright saves a screenshot after every browser action (with `-ok` / `-fail` in the
name), and the verifier captures the final interview calendar. The dashboard's **Evidence**
panel shows them; the files are in `screenshots/`. Every run is also stored in SQLite with its
full event log and an action ledger (input, result, error, recovery link and screenshots per action)
in `details.actions`, available from `GET /api/runs/{id}`.

Single-candidate goals use the same per-step confirmations (exactly one matching interview,
the status, the approved invitation), but not the before/after verifier.

## Testing

All tests start their own demo-app server on port 5065 and drive headless Chromium; nothing is mocked except where noted.

```bash
cd backend && ../.venv/bin/python -m pytest -q     # 49 tests: agent, recovery, approval, end-to-end
cd .. && .venv/bin/python -m pytest -q automation/tests   # 4 tests: the Playwright layer
.venv/bin/python -m pytest -q demo-app/tests              # 9 tests: the demo app on its own (fast, no browser)
cd frontend && npx tsc --noEmit && npm run lint    # frontend type check and lint
```

Run one pytest session at a time (they share port 5065).

What the end-to-end suite (`backend/tests/test_end_to_end.py`) proves through the real API:

1. **Normal run**: candidates found in the UI, interviews tomorrow afternoon around an existing booking, statuses updated, two approvals that each resume the run, all checks pass, evidence screenshot exists.
2. **Variation**: Full Stack Engineer, 45-minute slots back to back on Friday, different hiring manager, the `applied` candidate left alone.
3. **Failure**: saved-but-failed is detected by a state check and not retried (no duplicate); a transient failure is retried once.
4. **Pause**: nothing changes while paused; continue finishes with no duplicates.
5. **Unrecoverable failure**: PARTIALLY_COMPLETED (one candidate blocked) or FAILED (all blocked), never COMPLETED, with the reason per candidate.

Plus re-running a finished goal (does nothing), rejecting one invitation (PARTIALLY_COMPLETED), and rejecting every
invitation (PARTIALLY_COMPLETED: the interviews and statuses are real, only the emails were withheld).
`test_recovery_unit.py` uses in-memory fakes for the cases the demo app can't produce
(unreadable page after a failure, permanent error).

## Configuration

`.env` (copied from `.env.example`) configures the backend:

| Variable | Default | Purpose |
|---|---|---|
| `DEMO_APP_URL` | `http://127.0.0.1:5050` | The demo app the agent operates |
| `AGENT_HEADLESS` | `true` | `false` opens a visible Chromium window |
| `AGENT_MAX_ATTEMPTS` | `3` | Attempts for a failing action |
| `AGENT_RETRY_BACKOFF_SECONDS` | `1.0` | Wait between attempts (grows linearly) |
| `AGENT_STEP_DELAY_SECONDS` | `0.3` | Pause after each logged step, so runs are watchable |
| `DATABASE_URL` | `backend/opspilot.db` | SQLite file for runs and approvals |
| `CORS_ORIGINS` | `http://localhost:3000,…` | Origins allowed to call the API |

`frontend/.env.local` sets `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).

## Project layout

```
backend/
  app/main.py            API: runs, controls, approvals, /screenshots
  app/verification.py    independent before/after verification → final status
  app/agent/
    runner.py            goal → plan → act → verify; batch and single-candidate flows
    goal.py              rule-based goal parser (batch + single)
    planning.py          free-slot finding around existing bookings
    scheduling.py        interview creation with state-aware recovery
    invitation.py        approval-gated invitation email
    control.py           pause / stop / approval gate (ApprovalGrant)
    manager.py, store.py run lifecycle and persistence
    driver.py            BrowserDriver (Playwright) + HarnessClient (admin, /api/state)
    evidence.py          action ledger
    events.py            structured event log (also one JSON line per event in the server log)
    scenarios.py         reproducible failure scenarios
  tests/                 pytest suites (see Testing)
automation/              Playwright layer: RecruitmentBrowser → ActionResult (+ its own tests)
demo-app/app/            the synthetic recruitment app the agent operates
frontend/                Next.js dashboard (app/page.tsx, app/panels.tsx, lib/)
screenshots/             evidence written at run time (git-ignored)
```

## Known limitations

- **One workflow, one app.** Only interview scheduling (plus status update and invitation) in the bundled synthetic app.
- **No LLM and no LangGraph.** The agent is a plain Python pipeline (parse → plan → act → verify) with an explicit event log and checkpoint; there is no model call and no agent framework. The dashboard uses CSS modules, not Tailwind.
- **Rule-based goal understanding.** Regular expressions, not an LLM. Phrasings outside the documented patterns are refused with a reason rather than guessed.
- **Simple scheduling model.** Only the interviewer's calendar is considered: no candidate availability, time zones, or holidays. Relative dates that land on a weekend move to the next business day; an explicit date or weekday is taken as written, even on a weekend.
- **Emails are not really sent.** The demo app records an invitation as "sent"; nothing leaves the machine.
- **One run at a time**, and one approval per email (no bulk approve).
- **Pause is between steps**, not mid-action: an action already in the browser finishes first.
- **The before/after verifier covers batch goals only.** Single-candidate goals rely on per-step confirmations.
- **Verification reads a demo-only endpoint** (`/api/state`). A real system would use the product's read API or re-read the UI.
- **No authentication** on the dashboard, the API, or the demo app's `/admin/*` and `/api/state` endpoints (the harness uses them to reset data and arm failures). Everything binds to localhost and is meant for local use only.
- **The demo app's data is in memory**: restarting it resets everything. Dates like "tomorrow" are relative to the day you run it.
- **The dashboard polls** every 0.5 s instead of streaming.
- **Screenshots are never cleaned up** (the `screenshots/` folder only grows).

## What I personally built

> ✏️ **To be completed by Priyansh before submitting.** Describe in your own words what you
> designed, decided, wrote, reviewed and debugged yourself, for example: choice of the
> recruiting workflow and the synthetic app, the phase plan, the recovery and approval rules,
> what you changed after reviewing generated code, and what you tested by hand.

## AI and tool assistance

- **Claude Code (Anthropic)** was used heavily, across several parallel sessions, to generate and revise most
  of the code, tests and documentation in this repository, phase by phase (foundation, demo app,
  Playwright layer, failure recovery, human control, verification, end-to-end testing). Its output
  was directed by phase specifications and reviewed by the author.
- **Libraries:** FastAPI, Pydantic, SQLAlchemy, Jinja2, Playwright, pytest, Next.js and React. No
  code was copied from other projects.
- **No AI at run time.** OpsPilot itself makes no model calls; its behaviour is deterministic.

## Future improvements

- An LLM planner behind the same plan format (with the rule-based parser as a fallback), so more phrasings and workflows work.
- Run the before/after verifier for single-candidate goals too, and show the action ledger as its own table in the dashboard.
- Candidate availability and time zones in slot finding; reschedule and cancel flows (with approval).
- One approval for a batch of emails, with per-email opt-out.
- Server-sent events instead of polling; authentication and an audit trail of who approved what.
- Point the same driver interface at a second real-world app (e.g. a calendar or ATS sandbox).
- Screenshot retention policy and a per-run evidence bundle download.
