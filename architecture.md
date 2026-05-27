# Architecture

This document describes how the Debate Allocator fits together — the
shape of each layer, the contracts between them, and the invariants that
must hold for the system to keep working. It exists so that future
changes (especially AI-assisted ones) don't drift components out of
sync. **If you change a contract here, change every component listed
under "Touchpoints" for that contract.**

---

## 1. Stack at a glance

| Layer            | Technology                                       |
| ---------------- | ------------------------------------------------ |
| Server           | FastAPI 0.115 on uvicorn, Python 3.12            |
| Storage          | SQLite (single file, WAL mode) — one DB per box  |
| Allocation       | OR-Tools CP-SAT (`ortools` 9.15)                 |
| Templates        | Jinja2 (server-rendered HTML shells)             |
| Frontend (admin) | Alpine.js 3 + SortableJS, vanilla `fetch`        |
| Frontend (part.) | Vanilla JS, Tailwind via CDN-style local file    |
| Offline          | Service worker caches the `/register` shell only |
| Packaging        | Single-stage Dockerfile, `python:3.12-slim`      |

Everything is local-friendly: no CDNs (Tailwind, Alpine, Sortable, QRCode
are vendored under `app/static/vendor/`), no third-party JS at runtime.

---

## 2. Layered structure

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser                                                            │
│  ─ landing.html      → POST /events                                 │
│  ─ register.js       → POST /api/events/{code}/participants         │
│  ─ waiting.js        → polls /api/events/{code}/public  (every 1 s) │
│  ─ admin.js (Alpine) → /api/admin/{admin_token}/*                   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP (JSON in/out, HTML for pages)
┌──────────────────────────────▼──────────────────────────────────────┐
│  FastAPI app (app/main.py)                                          │
│   routers/pages.py    — HTML pages + /events + /sw.js               │
│   routers/events.py   — public participant API                      │
│   routers/admin.py    — admin-token-gated API                       │
│   deps.py             — ConnDep, EventDep, AdminEventDep            │
│   models.py           — Pydantic DTOs + Literal types               │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ sqlite3.Connection (per-request)
┌──────────────────────────────▼──────────────────────────────────────┐
│  Business / DAO layer                                               │
│   db.py      — schema, migrations, thin SQL CRUD helpers            │
│   solver.py  — CP-SAT propose_rooms / fill_remaining + slot ops     │
│   settings.py — env-driven config (DATA_DIR, BASE_URL)              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│  SQLite — $DATA_DIR/tournaments.db (PRAGMA journal_mode = WAL)      │
│   events · participants · rooms · slots                             │
└─────────────────────────────────────────────────────────────────────┘
```

**Rule of thumb for where new code goes:**

- New SQL → `db.py` if it's CRUD; `solver.py` if it touches the
  room/slot allocation problem.
- New endpoint → the matching router. `pages.py` for HTML, `events.py`
  for public (event-code) API, `admin.py` for token-gated API.
- New DTO or literal → `models.py`. If you change a literal, also update
  the matching `CHECK` in `db.py` (see §6 invariants).
- Business decisions in routers, not in `db.py`. `db.py` is "SQL in,
  rows out". Routers compose `db` + `solver` calls and translate HTTP
  status codes.

---

## 3. Source layout

```
app/
  main.py              FastAPI app factory + lifespan (runs db.migrate())
  settings.py          Frozen dataclass: DATA_DIR, BASE_URL, db_path
  deps.py              ConnDep, EventDep (by code), AdminEventDep (by token)
  models.py            Pydantic DTOs and shared Literal types
  db.py                SQLite connection, schema, CRUD helpers, token gen
  solver.py            CP-SAT phase 1+2, slot manipulation, get_rooms
  routers/
    pages.py           HTML pages, /events POST, /sw.js
    events.py          /api/events/{code}/public, /participants, /me
    admin.py           /api/admin/{admin_token}/...
  templates/           Jinja2 (landing, created, register, waiting,
                       rooms, admin, legal)
  static/
    css/               admin.css (admin panel), participant.css (rooms)
    js/                admin.js, register.js, waiting.js, sw.js
    vendor/            alpine, sortable, qrcode, tailwind (local copies)
    fonts/, assets/

tests/                 Smoke shell scripts + test_solver.py
legacy/                Original prototype solver — reference only,
                       NOT imported by the app
data/                  SQLite DB lives here (mount as a volume in prod)
```

`legacy/` is read-only history. Do not import from it.

---

## 4. Domain model

Four tables. All defined in `_SCHEMA` in `db.py`; schema is idempotent
and runs on every startup from `main.lifespan`.

```
events                    1 ── N   participants
  code         PK 9-digit          event_code  FK→ events.code
  admin_token UNIQUE                browser_token   (per-device id)
  status      open|closed|published
  room_limit  default 5
  reg_deadline (epoch)

events                    1 ── N   rooms              1 ── N   slots
                                    event_code FK              room_id FK
                                    format BP|OPD              role speaker|judge
                                    language DE|EN             subrole
                                    name (optional)            participant_id FK (nullable, UNIQUE)
                                                               locked 0|1
```

Key constraints (all enforced in SQL — do not weaken):

- `events.code` is exactly 9 digits, matched by `GLOB`.
- `participants UNIQUE(event_code, name)` — name collision is a 409.
- `participants UNIQUE(event_code, browser_token)` — same device, same
  event ⇒ same participant row. This is what makes the modify-flow work.
- `slots UNIQUE(participant_id)` — a participant can occupy at most one
  slot. `solver.assign_participant` enforces this by clearing the prior
  slot first.
- `ON DELETE CASCADE` on `rooms.event_code` and `slots.room_id`.

---

## 5. Lifecycle and state machine

```
        (admin clicks Create)
                │
                ▼
   ┌────────────────────────┐         participants register/modify
   │ events.status = open   │ ◄────── via POST /api/events/{code}/participants
   └───────────┬────────────┘         (browser_token = identity)
               │ POST close-registration
               ▼
   ┌────────────────────────┐         room/slot edits allowed here.
   │ events.status = closed │ ◄────── _require_closed gate in admin.py
   └───────────┬────────────┘         enforces this for ALL slot/room ops.
               │ POST publish
               ▼
   ┌────────────────────────┐         waiting.js poll sees status flip,
   │ events.status=published│         redirects to /rooms.
   └────────────────────────┘
```

The status field is the source of truth for what's permitted:

| Status      | participants can register? | admin can edit rooms/slots? | admin can edit deadline / room_limit? |
| ----------- | -------------------------- | --------------------------- | ------------------------------------- |
| `open`      | yes                        | no                          | yes                                   |
| `closed`    | no (modify still 409s)     | yes                         | yes                                   |
| `published` | no                         | no                          | no                                    |

These rules are enforced at exactly **one** site each:

- "registration is open" → `submit_participant` in `events.py`.
- "must be closed" → `_require_closed` in `admin.py` (called by every
  room/slot mutation).
- "not published" → `patch_event` in `admin.py`.

If you add a new mutation, route the gate through these helpers instead
of inlining a `status != …` check.

---

## 6. Cross-layer invariants (do not break these)

These are the contracts that hold the system together. Each is a single
point of truth in code; if it drifts, the system stops working.

### I1. Enum vocabularies match between SQL `CHECK` and Pydantic `Literal`

| Domain   | SQL (`db.py`)                          | Pydantic (`models.py`)                       |
| -------- | -------------------------------------- | -------------------------------------------- |
| Status   | `events.status IN ('open','closed','published')` | `EventStatus`                          |
| Language | `participants.language IN ('DE','EN','DE/EN')`   | `Language`                             |
|          | `rooms.language IN ('DE','EN')`        | `RoomLanguage`                                |
| Format   | `participants.format IN ('BP','OPD','egal')`     | `Format`                               |
|          | `rooms.format IN ('BP','OPD')`         | `RoomFormat`                                  |
| Role     | `participants.role IN ('S','J','SJ')`  | `RoleChoice`                                  |
|          | `slots.role IN ('speaker','judge')`    | `SlotRole`                                    |
| Exp.     | `experience IN (1,2,3)`                | `Experience`                                  |

If you add a new value, update **both** rows of the table.

### I2. Admin token is the only secret

The admin token is the sole gate for `/api/admin/*`. There are no
sessions, cookies, or accounts. The token appears only in the URL path
and is resolved by `require_admin` (deps.py).

`ParticipantDTO` never includes `browser_token` (it's per-device, must
not leak). `AdminStateResponse` is the only place where every
participant in the event is exposed at once — it is correctly behind the
admin gate.

### I3. Browser token is the participant's identity

A participant is identified by `(event_code, browser_token)`. The token
is generated client-side in `register.js` (32 hex chars), stored in
`localStorage` under `bt_{eventCode}`, and sent back on every
participant-side request.

- First POST creates the participant.
- Subsequent POST with the same token updates that row (modify flow).
- `?modify=1` in the register URL toggles the modify UI; without an
  existing token, the user is treated as fresh.

Do not bind participant identity to anything else (no IP, no name) —
name collisions are a UX-level 409, not an auth signal.

### I4. Transaction boundary is the request

`db.get_conn` (deps `ConnDep`) yields one `sqlite3.Connection` per
request, commits on clean return, rolls back on exception. Routers and
the solver share that one connection. The solver never opens its own
connection and never commits.

If you add background work, give it its own connection — do **not**
reuse the request connection across async boundaries.

### I5. Slot-mutation precondition is `_require_closed`

Every endpoint in `admin.py` that touches a room or slot calls
`_require_closed(event)` first. Do not bypass this — UI race conditions
otherwise let a publisher change layout out from under
already-rendered participant pages.

### I6. `propose_rooms` is destructive on rooms

`solver.propose_rooms` deletes all existing rooms for the event before
writing the new skeleton (`DELETE FROM rooms WHERE event_code = ?`).
Slot rows go too via `ON DELETE CASCADE`. The admin UI calls this only
from "Propose Rooms" — if you add a new caller, surface that destruction
in the UI.

`fill_remaining` (Magic Fill) is the non-destructive counterpart: it
clears unlocked slots only and respects every `locked=1` slot as a hard
pin.

### I7. Locked-slot semantics

`locked = 1` means "this participant stays in this slot, regardless of
re-fills". Rules baked into `solver.py`:

- `clear_slot` always sets `locked = 0` (a lock on an empty slot is
  meaningless and would block future fills).
- `assign_participant` clears any previous slot the participant sat in,
  including its lock — moving a person re-pins them only at the
  destination, not the origin.
- `fill_remaining` step 1 is `UPDATE … WHERE locked = 0`, so locked
  slots are untouched.

### I8. `slots.UNIQUE(participant_id)` is the single-seat guarantee

A participant can be in at most one slot. The solver's
`assign_participant` performs `UPDATE slots SET participant_id = NULL
WHERE participant_id = ?` before the new assignment so the unique
constraint never fires from a re-seat.

Do not insert into `slots` with a non-null `participant_id` directly —
go through `solver.assign_participant`.

---

## 7. HTTP surface (the contract between JS and Python)

Anything in this table is called by the frontend; anything missing here
that's defined in a router has no caller and is dead weight.

### 7.1 Pages (HTML, in `routers/pages.py`)

| Method | Path                          | Purpose                                                                  |
| ------ | ----------------------------- | ------------------------------------------------------------------------ |
| GET    | `/`                           | Landing — enter an event code, or POST `/events` to create one           |
| POST   | `/events`                     | Creates an event, 303-redirects to `/created/{admin_token}`              |
| GET    | `/created/{admin_token}`      | Success page showing both URLs (admin + participant) and QR              |
| GET    | `/register`                   | Static shell — JS reads `?event=…`; cached by SW                          |
| GET    | `/waiting`                    | Static shell — JS reads `?event=…` and `localStorage`                     |
| GET    | `/rooms?event=…&me=…`         | Server-rendered room list (post-publish view)                            |
| GET    | `/admin/{admin_token}`        | Admin panel shell (state is fetched via XHR by Alpine)                   |
| GET    | `/legal`                      | Static legal page                                                        |
| GET    | `/sw.js`                      | Service worker (served from root scope, `Service-Worker-Allowed: /`)     |
| GET    | `/healthz`                    | `{"ok": true}` — used by Docker `HEALTHCHECK`                            |

### 7.2 Public API (in `routers/events.py`, prefix `/api/events`)

| Method | Path                                  | Used by               | Notes                                      |
| ------ | ------------------------------------- | --------------------- | ------------------------------------------ |
| GET    | `/{code}/public`                      | waiting.js, register.js, landing.html | Polled every 1 s on /waiting. |
| POST   | `/{code}/participants`                | register.js           | Insert-or-update via browser_token.        |
| GET    | `/{code}/participants/me?token=…`     | waiting.js, register.js (modify) | Returns `ParticipantWithSlot`.  |

### 7.3 Admin API (in `routers/admin.py`, prefix `/api/admin`)

All admin endpoints return the **full** `AdminStateResponse` so the
client can re-render after any mutation without a separate refresh
round-trip. Keep this property when adding endpoints.

| Method | Path                                          | Status gate    | UI trigger                  |
| ------ | --------------------------------------------- | -------------- | --------------------------- |
| GET    | `/{admin_token}/state`                        | any            | boot + refresh              |
| POST   | `/{admin_token}/close-registration`           | `open`         | Close button                |
| POST   | `/{admin_token}/reopen-registration`          | `closed`       | Reopen button               |
| PATCH  | `/{admin_token}/event`                        | not published  | Deadline input              |
| POST   | `/{admin_token}/propose-rooms`                | `closed`       | Propose Rooms (destructive) |
| POST   | `/{admin_token}/rooms`                        | `closed`       | Add Room dialog             |
| PATCH  | `/{admin_token}/rooms/{room_id}`              | `closed`       | Inline rename               |
| DELETE | `/{admin_token}/rooms/{room_id}`              | `closed`       | Delete room                 |
| POST   | `/{admin_token}/rooms/{room_id}/judge-slots`  | `closed`       | + Judge button              |
| PATCH  | `/{admin_token}/slots/{slot_id}`              | `closed`       | Drag-drop, lock, clear      |
| POST   | `/{admin_token}/clear-unlocked`               | `closed`       | Clear unlocked button       |
| POST   | `/{admin_token}/magic-fill`                   | `closed`       | Magic Fill                  |
| POST   | `/{admin_token}/publish`                      | `closed`       | Publish                     |
| POST   | `/{admin_token}/seed-demo`                    | `open`         | +30 demo (testing)          |

If you add a new admin endpoint, also add it to `admin.js` (or remove it
if it's not called). The "every state-mutating endpoint returns full
state" property keeps the UI/server in sync without WebSockets — keep
it.

---

## 8. Solver design (`app/solver.py`)

The solver is a two-phase pipeline over the same SQLite tables. It has
**no** I/O of its own beyond the connection the router passes in.

### Phase 1 — `propose_rooms(conn, code)`

A pure CP-SAT model decides:

- the *number* of rooms (≤ `events.room_limit`),
- each room's `format` (BP/OPD) and `language` (DE/EN),
- which participant sits in which room and whether as speaker or judge.

The output is then sliced into the canonical slot layout
(BP_SPEAKER_SUBROLES / OPD_SPEAKER_SUBROLES / Panelist+Judge) by
`_split_speakers_into_slots` / `_split_judges_into_slots` and written to
`rooms`/`slots`.

Important: phase 1 wipes the event's existing rooms (see I6). It is the
"start over" button.

### Phase 2 — `fill_remaining(conn, code)`

Respecting `locked = 1` slots as fixed, it:

1. clears every unlocked filled slot,
2. solves a smaller bipartite assignment of remaining unplaced
   participants to remaining open slots,
3. writes the result back.

Soft costs in both phases (see the `W_*` constants at the top of
`solver.py`) are the only place to tune solver behaviour. Hard
constraints are intentionally minimal — over-judged BP rooms, for
instance, are handled by making "pure judge in a speaker chair" a soft
penalty rather than a hard ban, so the solver can degrade gracefully
instead of going infeasible.

### Slot-manipulation helpers (called by `admin.py`)

```
clear_slot(conn, slot_id)            → sets participant_id = NULL AND locked = 0
assign_participant(conn, slot_id, p) → vacates prior slot, assigns new
set_slot_locked(conn, slot_id, b)    → toggles lock flag
add_room(conn, code, format, lang)   → inserts empty room + slot skeleton
delete_room(conn, code, room_id)     → cascade-deletes slots
get_rooms(conn, code)                → reads the full room/slot/participant tree
```

`get_rooms` is the canonical read shape used by the rooms-template, the
admin state, and the `/me` slot lookup. If you add a slot attribute,
add it here and in `models.SlotDTO`.

---

## 9. Frontend behaviour worth knowing

### Admin panel (`admin.js` + `admin.html`)

- One Alpine component `adminPanel(adminToken)` holds all state.
- Every mutation goes through `send()` which posts JSON, parses
  `AdminStateResponse`, and re-assigns `event/participants/rooms`.
  This is why every admin endpoint returns full state.
- SortableJS instances are torn down and rebuilt on every state change
  (Alpine `$watch` on `rooms` and `participants`).
- Drag-drop is **only** wired up when `event.status === 'closed'`. In
  other states the panel is read-only.
- Optimistic UI is intentionally avoided — the server is always the
  source of truth after a mutation.

### Participant flow

- `register.js` writes `bt_{eventCode}` to `localStorage` on success.
- `waiting.js` polls `/api/events/{code}/public` every 1 s; on
  `status === "published"` it `location.replace`s to `/rooms`.
- The 1-Hz poll is the only "realtime" mechanism — no WebSocket. If
  load grows, this is the first thing to swap.

### Service worker (`sw.js`)

- Scope: root (`Service-Worker-Allowed: /`).
- Caches the `/register` shell + its static assets only.
- Never caches `/api/*` — all data is fetched fresh.
- Bump `CACHE_VERSION` in `sw.js` when you change cached assets.

---

## 10. Configuration and deployment

`settings.py` reads exactly two env vars:

| Env var    | Default                  | What for                                                    |
| ---------- | ------------------------ | ----------------------------------------------------------- |
| `DATA_DIR` | `./data`                 | Where `tournaments.db` lives. Mount as a volume.            |
| `BASE_URL` | `http://localhost:8000`  | Public URL for QR codes / share links. No trailing slash.   |

The Dockerfile sets `DATA_DIR=/data` and declares it as a volume. The
SQLite database file is the only persistent state. There are no
migrations beyond the idempotent `_SCHEMA` and the additive `name`
column on `rooms`; future schema changes belong in `db.migrate()` and
must be additive (no destructive ALTER).

---

## 11. Tests

| File                       | What it covers                                                       |
| -------------------------- | -------------------------------------------------------------------- |
| `tests/test_solver.py`     | Solver correctness — runs CP-SAT against generated participants.     |
| `tests/smoke_api.sh`       | Public API happy paths (events, participants, /me).                  |
| `tests/smoke_pages.sh`     | HTML pages return 200 with the right shells.                         |
| `tests/smoke_admin.sh`     | Admin lifecycle: open → close → propose → fill → publish.            |
| `tests/smoke_db.py`        | DB layer + token generators.                                         |

The smoke scripts build the Docker image automatically on first run.
The solver test needs `ortools` (easiest via the image — see README).

---

## 12. Anti-drift checklist

Run through this any time you add, rename, or remove something. The
goal is to catch the ai-slop pattern of "one layer changed, the others
silently desynced".

- **New SQL column.** Add a `CHECK` if it has an enum domain. Update the
  matching `Literal` in `models.py`. Surface it in `get_rooms` or the
  appropriate DAO read. Echo it in the DTO. Render or accept it in
  `admin.js` / templates. Bump the SW cache if a static asset changes.
- **New admin endpoint.** Pick the right status gate (`_require_closed`
  is the usual one). Return `AdminStateResponse`. Add a button or
  trigger in `admin.js`. If it's destructive, confirm in JS first.
- **New admin button in JS.** Make sure the endpoint exists. Make sure
  the button's `:disabled` matches the server-side gate — never have a
  button that the server will reject.
- **New participant field.** Update the SQL CHECK, the Pydantic models
  (`ParticipantSubmitRequest`, `ParticipantDTO`), `insert_participant`,
  `update_participant_by_browser_token`, the solver tuple-shape (see
  `_load_participants` — the array order matters), `register.js`
  serialization, and the admin participant card.
- **New page.** Add it to `routers/pages.py`. Decide whether the SW
  should cache it (probably no, unless it's a static shell). Add it to
  the URL table in the README.
- **New literal value.** Update both rows of the table in §I1.
- **Removed something.** Grep for it — `rg <name> app/` — across Python,
  Jinja, and JS before deleting. Dead code that still has a frontend
  caller turns into 404s in the browser; dead code that's only on the
  backend is harmless but should still be removed.

---

## 13. What is intentionally *not* here

This is a small app for a club. The following are deliberate omissions,
not gaps:

- **No accounts, no email.** Auth is the admin URL + the browser token.
- **No WebSockets.** Waiting page polls every second.
- **No background jobs.** All solver runs happen in the request thread.
  This is acceptable because event sizes are bounded (~50 participants,
  ≤5 rooms) and CP-SAT finishes in seconds with a 30 s hard cap.
- **No multi-DB / no sharding.** Single SQLite file. One event = one row
  + cascaded children.
- **No tracing / structured logs.** `uvicorn` access logs are enough.

If any of these become a problem, that is a real architectural decision
and should land in this document before the code lands.
