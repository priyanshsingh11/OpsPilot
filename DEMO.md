# Recording the 5-minute demo

A script for one take that shows: (1) a normal task, (2) a variation without code changes,
(3) failure + recovery, (4) human approval, (5) verification and evidence. Every run starts
from the same seed data and the failures are count-based, so each take behaves the same.

## Before you record (once)

1. Complete [Install](README.md#install) from the README.
2. In `.env`, set:
   ```
   AGENT_HEADLESS=false          # a visible Chromium window shows the agent clicking
   AGENT_STEP_DELAY_SECONDS=0.6  # slower steps, easier to narrate
   ```
3. Start the three processes (README → [Run](README.md#run)): demo app on 5050, backend on 8000, dashboard on 3000.
4. Arrange the screen: dashboard on the left, the agent's Chromium window on the right (it
   opens when a run starts). Optionally keep a tab on http://127.0.0.1:5050/interviews.
5. Do a full dry run of the steps below once, so Chromium and Next.js are warmed up.

**Any day works:** "tomorrow" always resolves to the next business day (a Saturday or Sunday
moves to Monday, and the plan says so), and the demo app always seeds one booking there for
Priya Nair at 14:00–15:00, so slot finding visibly works around it. "Friday" means the next
Friday after today.

For every run below: keep **Reset demo data first** ticked.

## The script (≈ 5:00)

### 0:00–0:25 · Intro
"OpsPilot takes a plain-English goal, operates a recruitment app in a real browser, recovers from
failures without duplicating work, asks before anything irreversible, and proves the result."
Show the dashboard and, briefly, the demo app's jobs page.

### 0:25–1:45 · 1. Normal task (+ 4. human approval)
- Goal: `Schedule interviews for all shortlisted AI Engineer candidates tomorrow afternoon.`
- Scenario: **Normal run** → **Run agent**.
- Point out, as they appear:
  - Timeline: *Filtered … found 2 (Aarav Sharma, Diya Patel)*, then *Priya Nair is busy … at 14:00*,
    then the **PLAN**: 13:00 and 15:00, which work around the existing booking.
  - Chromium: the agent opening candidate pages, filling the form, updating the status.
  - Progress panel: each candidate ticks to **Done**.
- **Approval** (status turns WAITING_FOR_APPROVAL): read out the email (To, Subject, Message)
  and the "If approved / If rejected" lines. "Nothing is sent until I approve this exact email."
  Click **Approve and send**; the run resumes. Approve the second email too.
- Result: **COMPLETED**.

### 1:45–2:30 · 5. Verification and evidence
- **Verification** panel: the 7 *Final verification* checks, all PASS: records exist, statuses,
  invitations, no duplicates, required fields, no double-booking, no unintended changes.
  "These come from comparing the app's records before and after, not from the agent's own claims."
- **Evidence** panel: scroll the screenshots; open the last one (the final calendar).
- Optional: switch to http://127.0.0.1:5050/interviews and show the two new rows.

### 2:30–3:10 · 2. Variation without code changes
- Goal: `Schedule 45-minute interviews for shortlisted Full Stack Engineer candidates on Friday.`
- Scenario: **Normal run** → **Run agent**. Approve both emails.
- Point out in the PLAN: different role, **45-min** slots back to back (10:00, 10:45),
  **Friday's** date, a different hiring manager (Neha Kulkarni); the *applied* candidate is not included.
- Result: **COMPLETED**.

### 3:10–4:10 · 3. Failure + recovery
- Goal: back to `Schedule interviews for all shortlisted AI Engineer candidates tomorrow afternoon.`
- Scenario: **Saved, but reported as failed** → **Run agent**.
- Narrate the hard case: "The calendar saves the first interview but tells us it failed. A
  blind retry would book Aarav twice."
- Point out in the **Failure and recovery** panel:
  `ACTION FAILED` → `STATE CHECK … interview int-1 matching the request already exists` →
  `RECOVERY STARTED … NOT retrying` (**Decision: no retry**).
- Approve both emails. Result: **COMPLETED**, with *No duplicate interviews or invitations: PASS*.

### 4:10–4:50 · Unrecoverable failure (honest reporting)
- Same goal, scenario **Outage for one candidate** → **Run agent**. Approve the one email it asks about.
- Aarav's three attempts fail (each followed by a state check); Diya goes through.
- Result: **PARTIALLY COMPLETED**, never "Completed". **What remains** says Aarav has no
  interview, why (service failed 3 times), and that it is safe to re-run.

### 4:50–5:00 · Close
"Real browser actions, safe recovery, approval before anything irreversible, and a final status
taken from the app's own records."

## Optional extras (if you have time, or for a second take)

- **Pause:** start the normal task and click **Pause** right away. The run holds at the next
  step boundary and the timeline stops growing. Click **Continue**: it carries on with no duplicates.
- **Reject:** in the normal task, **Reject** the first email. Nothing is sent to that candidate,
  and the run ends PARTIALLY COMPLETED, listing the unsent invitation. (Reject both: still PARTIALLY
  COMPLETED, because the interviews and statuses are real; no invitation is sent.)
- **Full outage:** scenario **Calendar outage** → **FAILED**, nothing created, nothing to approve.
- **Transient failure:** scenario **Calendar fails once** → the state check finds nothing,
  one retry, COMPLETED.

## If something looks off

- Dashboard says *Backend offline*: start the backend (port 8000) and refresh.
- Run ends with "Recruitment app unavailable": start the demo app on 5050 (`DEMO_APP_URL` in `.env`).
- No Chromium window: `AGENT_HEADLESS=false` must be in `.env` **before** starting the backend.
- Strange leftovers from an earlier take: tick **Reset demo data first**, or restart the demo app.
