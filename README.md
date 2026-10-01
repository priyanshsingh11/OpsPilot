# OpsPilot

Phase 1 foundation: Next.js dashboard + FastAPI backend + SQLite. No agent logic yet.

## Layout
- `backend/` — FastAPI app (`app/main.py`, `app/config.py`, `app/database.py`)
- `frontend/` — Next.js dashboard; API client in `frontend/lib/api.ts`
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

## Demo recruitment app (Phase 2)
A separate synthetic portal ("TalentDesk") that OpsPilot will operate via the browser. Own SQLite DB at `demo-app/data/recruit.db`, seeded on first run.

```bash
.venv/bin/pip install -r demo-app/requirements.txt
.venv/bin/uvicorn main:app --app-dir demo-app --port 5001    # http://localhost:5001
.venv/bin/python -m pytest demo-app/tests -q
```
Pages: `/` jobs, `/jobs/{id}?status=` candidates, `/candidates/{id}` details/status/scheduling, `/interviews` calendar, `/admin` failure toggle + reset, `/api/state` JSON snapshot (for verification code).
Failure simulation: set `SIMULATE_CALENDAR_FAILURE=true` (applies on seed/reset) or use `/admin`. One-shot by default; mode `before_write` or `after_write`. All UI elements carry `data-testid`.
