# Synthetic recruitment app

The application OpsPilot operates. A small FastAPI + Jinja2 app with server-rendered pages and
stable `data-testid` attributes. All data is synthetic, lives in memory, and is re-seeded on every
start (or `POST /admin/reset`). It is not part of the agent: the agent only sees it through a browser.

```bash
cd demo-app && ../.venv/bin/uvicorn app.main:app --port 5050     # http://127.0.0.1:5050/jobs
../.venv/bin/python -m pytest tests -q                           # run from demo-app/, or from the repo root
```

(Port 5000 is avoided because macOS AirPlay Receiver holds it.)

## Pages

| Route | Purpose |
|---|---|
| `/jobs`, `/jobs/{id}` | Jobs; a job's candidates, filter with `?status=` and `?q=` |
| `/candidates/{id}` | Profile, status form, interviews table, invitation form and table |
| `/candidates/{id}/interviews/new` | Schedule-interview form |
| `/interviews` | All interviews, plus the chaos-mode toggle and the failure-plan state |

## Failure simulation

- **Failure plan**: the next N interview creations fail, in `before_write` (nothing saved) or
  `after_write` (saved, but an error is returned) mode. Arm it with `POST /admin/failure`
  (`remaining`, `mode`) or at startup: `DEMO_FAILING_ATTEMPTS=1 DEMO_FAILURE_MODE=after_write`.
  The dashboard's "Failure scenario" picker uses the same endpoint.
- **Chaos mode**: the toggle on `/interviews` makes every creation fail until switched off.

Like a real mail or calendar service, the app does **not** dedupe: creating or sending twice
creates two records. Preventing that is the agent's job.

## Harness endpoints (local use only, no authentication)

`POST /admin/reset`, `POST /admin/failure`, `POST /admin/chaos`, and the read-only `GET /api/state`
JSON snapshot used by the verifier.
