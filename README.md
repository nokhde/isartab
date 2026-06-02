<img src="app/static/assets/isartab_logo.png" alt="isartab logo" width="33%">

> ### Live demo: **[isartab.nuh.me](https://isartab.nuh.me)**
> Try it out — create an event and play around. (Don't use the demo for a real event)

> **New to this kind of thing?** If you're not a programmer, follow
> [noobtutorial.md](noobtutorial.md) instead — it walks you through
> deployment step by step with no jargon.

# Debate Allocator

A small web app for the **Munich Debating Club** to run debate evenings:
participants register from their phones, the tabmaster proposes rooms
(with an OR-Tools CP-SAT solver), drags people around, locks slots, and
publishes — participants see their room appear on their phone.

No accounts, no emails. Each event has a code; each admin gets a secret
link; each browser identifies itself with a local token.

---

## Run it in one minute (Docker)

If you have Docker installed, this is the easiest way:

```bash
docker build -t debate-allocator .
docker run --rm -p 8000:8000 debate-allocator
```

Open <http://localhost:8000> and click "Create event". Done.

The database is **in-memory only** — nothing is written to disk, and all
events are wiped when the app restarts. This is intentional, for data
protection. There is no `data/` folder and no volume to mount.

## Run it without Docker

You need **Python 3.12** (ortools doesn't ship wheels for newer versions
yet). Then:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

---

## Deploy to a server (Coolify)

This repo ships with a `Dockerfile` and a `/healthz` endpoint, so any
Docker host works. For [Coolify](https://coolify.io) specifically:

1. **New Resource → Dockerfile** pointing at this repo.
2. **Port:** `8000`.
3. **Environment variable:** set `BASE_URL` to your public HTTPS URL
   (e.g. `https://debate.example.org`, no trailing slash). This is what
   appears in share links and QR codes.

No persistent volume is needed: the database lives in memory only and is
wiped on every restart/redeploy (by design, for data protection).

That's it. After the first deploy, hit `BASE_URL/healthz` — you should
see `{"ok": true}`.

See [coolify.md](coolify.md) for screenshots/extra notes.

---

## URLs

| Path | What it is |
| --- | --- |
| `/` | Landing — create a new event |
| `/register?event={code}` | Participant form |
| `/waiting?event={code}` | Live waiting page |
| `/rooms?event={code}` | Public room view (after publish) |
| `/admin/{admin_token}` | Tabmaster panel |
| `/legal` | Impressum & privacy (uses `IMPRINT_TEXT`) |
| `/healthz` | Health check |

## Tokens & secrets

- **Event code** (9 digits) — public, in URLs.
- **Admin token** (32-char hex) — **secret**. Whoever has the admin link
  controls the event. Lose it and the event is unrecoverable.
- **Browser token** — stored in the participant's `localStorage`, so the
  same browser keeps identity across reloads.

## Tests

```bash
# Smoke tests (build the Docker image automatically on first run):
bash tests/smoke_api.sh
bash tests/smoke_admin.sh

# Solver test (needs ortools — easiest via the Docker image):
docker run --rm -v "$(pwd)":/repo -w /repo \
  -e PYTHONPATH=/repo \
  --entrypoint python debate-allocator tests/test_solver.py
```

## Project layout

```
app/        FastAPI app (routes, DB, solver, templates, static)
tests/      Smoke + solver tests
legacy/     Original solver + participant generator (kept for reference)
Dockerfile  Single-stage python:3.12-slim
coolify.md  Coolify deployment notes
architecture.md design doc
```
