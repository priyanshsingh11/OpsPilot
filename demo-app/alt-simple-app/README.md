# Alternate simple ATS (not used by OpsPilot)

An alternate single-file version of the recruitment app. It appeared in `demo-app/` on 2026-10-01 and
overwrote some of the main app's templates. It was moved here so it doesn't collide with the canonical app
(`demo-app/main.py` + `db.py`). It has no failure simulation.

Run (from repo root): `.venv/bin/uvicorn app:app --app-dir demo-app/alt-simple-app --port 5002`
