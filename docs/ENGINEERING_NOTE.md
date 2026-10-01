# OpsPilot: engineering note

## 1. Problem
Take a plain-English recruiting goal ("Schedule interviews for all shortlisted AI Engineer
candidates tomorrow afternoon"), do it in a real application through a real browser, survive a
flaky calendar without double-booking anyone, stop before sending email, and prove the outcome.

## 2. Design decisions
- **A synthetic app I control**, because the assignment asks for a test environment and because
  deterministic failure injection needs one. It has no dedupe, like a real calendar or mailer.
- **The browser performs every action; HTTP is used for two things only**: harness controls
  (reset, arm a failure) and a read-only state snapshot for verification.
- **Deterministic code, no model.** The goal parser, planner and recovery logic are ordinary
  Python. That makes behaviour reproducible and testable, at the cost of language coverage (§12).
- **Count-based failures, not random ones**, so a demo or test behaves the same every time.

## 3. Architecture
Dashboard (Next.js) → FastAPI backend → agent thread → Playwright → demo app. SQLite stores runs,
events, checkpoints and approvals. Verification is a separate module. Diagram and run lifecycle:
[ARCHITECTURE.md](ARCHITECTURE.md).

## 4. Agent design
`backend/app/agent/runner.py` runs: **parse the goal** (using names read from the app) → **discover**
(filter candidates, read the calendar) → **plan** (free slots around existing bookings) → **act per
candidate** (schedule, set status) → **ask approval and send** → **verify**. There is no LangGraph
and no agent framework; the state is an explicit event log plus a checkpoint saved after each
step. One candidate's failure is recorded and the rest continue.

## 5. Tool design
`automation/` exposes typed actions (`filter_candidates`, `create_interview`, `send_invitation`, …),
each returning an `ActionResult` (success, typed data, structured error with a category, screenshot).
No business rules live there. `BrowserDriver` adapts them for the agent. The agent has no filesystem,
database or code-execution access beyond these calls.

## 6. Browser automation
Chromium via Playwright's sync API, one browser per run, in the run's own thread. Selectors are
`data-testid`. Waiting is by element and navigation state with a 10 s default timeout; there are no
fixed sleeps in the browser layer. A screenshot is saved after every action, and a failure is
returned as data, not raised.

## 7. Failure recovery
After any failed create the agent re-reads the candidate page before deciding: already there → do
not retry; not there and transient → retry (bounded, with backoff); not there and permanent, or the
page unreadable → stop with a blocker. The same check runs before the first attempt, so re-running a
finished goal changes nothing. The hardest case is simulated explicitly: the app saves the record
and then returns an error.

## 8. Verification
`backend/app/verification.py` ignores what the agent says. It compares the app's records before and
after against the goal and runs seven checks (records exist, statuses, invitations, no duplicates,
required fields, no double-booking, nothing unintended changed). Final status is COMPLETED only when
every target is done and every check passes; otherwise PARTIALLY_COMPLETED or FAILED, with a plain
list of what remains.

## 9. Human-in-the-loop
Sending an invitation needs approval of the exact email. The send path takes an `ApprovalGrant`
that exists only after the decision is read back from SQLite as approved, and the driver re-checks
it at the moment of the action, so a rejected, cancelled or different-content approval cannot send.
Pause and stop apply between steps; an action already in the browser finishes first. A run that
dies with the backend is marked interrupted and can be resumed; every step re-checks state first.

## 10. Evidence
Screenshots of real pages after each browser action and of the final calendar, the structured event
timeline, the action ledger and the verification report. All are produced by actual actions; nothing
is synthesised after the fact.

## 11. Testing
About 60 tests, mostly integration: real FastAPI, a real agent thread, real Chromium, the real demo
app, with outcomes asserted in the app's own records. They cover goal parsing, recovery and
duplicate prevention, approval, reject, pause, stop, crash-and-resume, and the end-to-end scenarios
(normal, variation, failure, unrecoverable, pause, reject). A small unit suite uses fakes for cases the
demo app cannot produce (unreadable page, permanent error).

## 12. Limitations
- Goal understanding is regular expressions; unfamiliar phrasings are refused with a reason.
- One workflow, one synthetic app; the invitation is recorded, not emailed.
- Only the interviewer's calendar is considered (no candidate availability or time zones).
- One run at a time; the dashboard polls; no authentication (local only).
- The verifier reads a demo-only `/api/state` endpoint.
- Not used, despite being natural choices: an LLM, LangGraph, Tailwind.

## 13. AI assistance
Claude Code (Anthropic) generated and revised most of the code, tests and docs, phase by phase,
directed by written specifications and reviewed by me. OpsPilot itself makes no model calls.

## 14. My contribution
> ✏️ To be written by Priyansh in their own words before submitting: what you decided, what you
> reviewed and changed, what you debugged, and what you tested by hand. This section is left
> blank on purpose so it states only what is true.

## 15. What I would build next
An LLM planner that emits the same plan structure (rule-based parser as fallback), the before/after
verifier for single-candidate goals, candidate availability and time zones, one approval for a batch
of emails, streaming instead of polling, and a second real-world app behind the same driver.
