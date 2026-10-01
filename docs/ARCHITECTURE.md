# OpsPilot architecture

How a goal becomes verified work, and why the pieces are split the way they are.
For setup and usage see the [README](../README.md).

## Components

| Component | Code | Responsibility | Does not |
|---|---|---|---|
| Dashboard | `frontend/` | Enter goals, show live state, approve/reject, pause/stop, show verification and evidence | Make decisions; it only renders the run and sends controls |
| API + run manager | `backend/app/main.py`, `agent/manager.py`, `agent/store.py` | Start one run at a time in a background thread, persist events/checkpoints/approvals, route user controls | Touch the demo app |
| Agent | `backend/app/agent/runner.py` and helpers | Parse the goal, plan, act through the browser, recover from failures, request approvals | Decide the final status (verification does) |
| Automation layer | `automation/` | Playwright actions on the demo app's pages, returning `ActionResult` (data or structured error + screenshot) | Contain business logic or retries |
| Verification | `backend/app/verification.py` | Compare the app's records before vs after the run with what the goal asked for; set COMPLETED / PARTIALLY_COMPLETED / FAILED and "what remains" | Read the agent's own claims |
| Demo app | `demo-app/app/` | The synthetic recruitment system: jobs, candidates, interviews, invitations, failure injection, `/api/state` | Deduplicate anything (a blind retry really would duplicate) |

## A run, step by step (batch goal)

```
POST /api/runs {goal, scenario, reset_demo_data}
  └─ RunManager.start → new thread → launch Chromium → run_goal()
       1. SETUP       harness resets data / arms the failure scenario         (admin HTTP, logged as SETUP)
       2. understand  open /jobs in the browser → parse_batch_goal(job titles from the page)
       3. discover    filter the job's candidates by status in the UI         → targets
       4. baseline    read /api/state                                          → "before" snapshot
       5. plan        read the /interviews calendar → free_slots() around existing bookings → PLAN event
       6. for each target (checkpoint() between steps: pause/stop land here)
            schedule_interview()  state check → submit form → on failure: state check → decide
            ensure_status()       check → update only if needed → confirm
            → TARGET_DONE, or TARGET_BLOCKED (and carry on with the next candidate)
       7. for each scheduled target: send_invitation_with_approval()
            compose the exact email → request_approval() blocks → grant → send → confirm
       8. verify      read /api/state again → verification.verify(expectations, before, after)
       9. evidence    screenshot of the final calendar
      10. terminal    COMPLETED | PARTIALLY_COMPLETED | FAILED  (or STOPPED if the user stopped it)
```

A single-candidate goal ("Schedule a Technical Screen for Aarav Sharma with Priya Nair on …")
runs steps 1, 2, 6 and 7 for that one candidate and ends COMPLETED / BLOCKED / REJECTED / STOPPED;
it does not run the before/after verifier (see Known limitations in the README).

## The recovery rule

Creating an interview and sending an email are **not idempotent**, and the app does not
deduplicate. So a failed attempt never triggers a blind retry (`agent/scheduling.py`):

```
before acting:  read the candidate page → already there? skip (re-runs are safe)
attempt         submit the form
  success   →   confirm in the app's records
  failure   →   read the candidate page again
                  exists               → do not retry (the failed request was applied)
                  missing + transient  → wait, retry (bounded by AGENT_MAX_ATTEMPTS)
                  missing + permanent  → stop for this candidate with the reason
                  page unreadable      → stop: outcome unknown, a retry could duplicate
```

The same discipline is used for invitations (`agent/invitation.py`): check whether one was
already sent before asking for approval, and re-read the page after a failed send.

Every decision is an event (`ACTION_FAILED`, `STATE_CHECK`, `RECOVERY_STARTED` with
`decision=retry|no_retry|stop`, `RETRY`) and an entry in the action ledger
(`agent/evidence.py`), where a retry or post-failure check points to the record it recovers from.

## Human control and the approval gate

`agent/control.py` owns pause, stop and approvals for a run.

- `checkpoint()` is called between steps. Pause holds the thread there; stop raises
  `RunStopped`, which unwinds to the runner and reports what was done. An action already in the
  browser finishes first, so nothing is left half-done.
- `request_approval(action, payload, details)` stores a pending approval (payload hash included)
  and blocks until the user approves or rejects via the API.
- On approval it returns an `ApprovalGrant` bound to that payload hash. `BrowserDriver.send_invitation`
  refuses to act without a grant that still authorizes exactly that payload, re-checking the
  database at the moment of sending. Agent code has no other path to send.
- Events, a checkpoint and approvals are persisted as they happen. If the backend dies, the run
  is marked `interrupted` at startup and can be resumed; the state checks make the resumed run
  skip finished work, and an existing approval for the same email is reused.

## Verification and the final status

`verification.verify()` receives, per target candidate, what the goal requires (round, time,
duration, interviewer, target status, whether an invitation is expected) plus the before and after
snapshots. It does not see the agent's results, only its blocker messages, to explain *why*
something is missing.

Checks: interview records exist (exactly one matching), statuses updated, invitations sent,
no duplicates, required fields present, no interviewer double-booking, no unintended changes
(other candidates' statuses, interviews and invitations untouched).

Outcome: all targets done and all checks pass → COMPLETED; some targets done (or all done but
a safety check failed) → PARTIALLY_COMPLETED; none done → FAILED. Every incomplete target gets a
"what remains" line with the reason.

## Data

Backend SQLite (`backend/opspilot.db`):

- `agent_runs`: goal, scenario, status, summary, blocker, `details` (JSON: verification report,
  remaining items, action ledger, ids), `events` (JSON event log), `checkpoint` (JSON progress for resume).
- `approvals`: run id, action, exact payload + hash, what the user was shown, status and decision time.

Demo app: in memory (`demo-app/app/store.py`), re-seeded on start or `POST /admin/reset`. Seed
data is synthetic; one seed interview is placed on the next business day at 14:00 so slot
finding has a real conflict to avoid.

Evidence: PNG screenshots in `screenshots/` (one after every browser action, plus the final
calendar), served read-only by the backend at `/screenshots/{file}`.

## Design choices

- **Browser for actions, API only for verification.** The assignment is about operating
  applications; the agent uses the same pages a person would. The JSON snapshot is used only
  by the verifier, to get an independent view of the outcome.
- **Rule-based parsing.** Deterministic and testable for a fixed workflow, with no model or key
  required to run the project. The parser reads names from the app at run time, so new
  candidates, jobs or interviewers need no code change. The obvious next step is an LLM planner
  producing the same plan structure.
- **Per-candidate isolation in batches.** One candidate's failure is reported and the rest
  continue, which is what makes PARTIALLY_COMPLETED meaningful.
- **Deterministic failure injection.** Count-based failure plans (not random) so a demo or test
  behaves the same every time, including the hardest case: saved-but-reported-failed.
