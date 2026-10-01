# Synthetic Recruitment Application

A small FastAPI + Jinja2 app that simulates a recruiting system. It exists so
the OpsPilot browser automation layer (Phase 3) has a realistic application to
operate. All data is synthetic and lives in memory — it is re-seeded on every
process start.

## Run

```bash
cd demo-app
../.venv/bin/uvicorn app.main:app --port 5000
```

Then open http://127.0.0.1:5000/jobs.

## Pages

| Route | Purpose |
|---|---|
| `/jobs` | List all jobs |
| `/jobs/{job_id}` | Job detail + candidates; filter with `?status=` and `?q=` |
| `/candidates/{id}` | Candidate profile, status form, interviews |
| `/candidates/{id}/interviews/new` | Schedule-interview form |
| `/interviews` | All interviews + chaos-mode toggle |

## Chaos mode

The interviews page has a **chaos mode** toggle. While enabled, scheduling a
new interview fails with a `500` error page (`data-testid="error-banner"`).
This simulates an interview-service outage so the automation layer can be
tested against a realistic failure without any external dependencies.

## Deterministic failure plan (Phase 5)

Besides chaos mode, `POST /admin/failure` (`remaining`, `mode`) makes the next
N interview creates fail:

- `before_write` — nothing is saved, and a 500 is returned.
- `after_write` — the interview **is saved**, but a 500 is still returned (like a calendar
  sync timing out after commit). A blind retry would create a duplicate, because the app
  does not reject duplicates.

The plan is shown on `/interviews`. `POST /admin/reset` restores seed data and re-arms
the plan from `DEMO_FAILING_ATTEMPTS` / `DEMO_FAILURE_MODE`. `GET /api/state` returns a
read-only JSON snapshot that verification code uses.

## Data

Seeded in `app/models.py`: 5 jobs, 9 candidates across pipelines, 1 interview.
Candidate statuses: `applied`, `screening`, `shortlisted`, `interview`,
`offer`, `hired`, `rejected`.
