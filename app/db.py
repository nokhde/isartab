"""SQLite connection helper, schema migration, and DAO helpers.

DAO helpers are intentionally thin: SQL in, dict-like rows out. Business
logic stays in the routers / solver.
"""
from __future__ import annotations

import asyncio
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import AsyncIterator, Iterator, Optional

from .settings import settings

# ─── Schema ────────────────────────────────────────────────────────────────
# Matches proposal §3.1. Idempotent via IF NOT EXISTS.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    code           TEXT PRIMARY KEY
                   CHECK (length(code) = 9 AND code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
    admin_token    TEXT NOT NULL UNIQUE,
    created_at     INTEGER NOT NULL,
    reg_deadline   INTEGER,
    room_limit     INTEGER NOT NULL DEFAULT 5,
    status         TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open','closed','published'))
);

CREATE TABLE IF NOT EXISTS participants (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_code        TEXT NOT NULL REFERENCES events(code) ON DELETE CASCADE,
    browser_token     TEXT NOT NULL,
    name              TEXT NOT NULL,
    language          TEXT NOT NULL CHECK (language IN ('DE','EN','DE/EN')),
    format            TEXT NOT NULL CHECK (format IN ('BP','OPD','egal')),
    role              TEXT NOT NULL CHECK (role IN ('S','J','SJ')),
    could_speak_last  INTEGER NOT NULL DEFAULT 1,
    experience        INTEGER NOT NULL CHECK (experience IN (1,2,3)),
    special_request   TEXT,
    forced_judge_last INTEGER NOT NULL DEFAULT 0,
    created_at        INTEGER NOT NULL,
    UNIQUE (event_code, name),
    UNIQUE (event_code, browser_token)
);

CREATE TABLE IF NOT EXISTS rooms (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_code      TEXT NOT NULL REFERENCES events(code) ON DELETE CASCADE,
    room_index      INTEGER NOT NULL,
    format          TEXT NOT NULL CHECK (format IN ('BP','OPD')),
    language        TEXT NOT NULL CHECK (language IN ('DE','EN')),
    name            TEXT,
    UNIQUE (event_code, room_index)
);

CREATE TABLE IF NOT EXISTS slots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id         INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('speaker','judge')),
    subrole         TEXT NOT NULL,
    slot_index      INTEGER NOT NULL,
    participant_id  INTEGER REFERENCES participants(id) ON DELETE SET NULL,
    locked          INTEGER NOT NULL DEFAULT 0,
    UNIQUE (participant_id)
);

CREATE INDEX IF NOT EXISTS idx_participants_event ON participants(event_code);
CREATE INDEX IF NOT EXISTS idx_rooms_event ON rooms(event_code);
"""


# ─── Connection plumbing ───────────────────────────────────────────────────
# One in-memory database for the whole process. Data lives only in RAM and
# is wiped when the process exits — by design, for data protection.
#
# We deliberately use a SINGLE shared connection rather than one-per-request:
#   * a private ":memory:" database is only visible to the connection that
#     created it, so per-request connections would each see an empty DB;
#   * the shared-cache alternative ("cache=shared") lets connections share a
#     DB but raises "database table is locked" under concurrent access.
# So we keep one connection open for the process lifetime and serialise all
# access to it, allowing only one transaction at a time. check_same_thread=False
# is required because FastAPI runs sync endpoints across a thread pool. This is
# more than fast enough for this app's scale (a single club evening).
#
# THE LOCK MUST BE AN asyncio LOCK AWAITED ON THE EVENT LOOP — NOT a
# threading lock inside a sync dependency. This caused a total outage
# (2026-07-01): FastAPI runs sync yield-dependencies through the anyio
# threadpool, which is capped at 40 tokens, and a request blocked on a
# threading lock inside that dependency pins a token for as long as it waits.
# The waiting page polls /public every second per open tab, so while one
# admin request held the lock for a ~10 s solver run, 40+ polls piled up and
# pinned every token. The lock-holder then needed one more token to run its
# own handler (run_in_threadpool) — held by requests waiting on the very lock
# it owned. Circular wait, permanent deadlock: no exception, no log line,
# /healthz (also threadpool-bound) starved too, container unhealthy, restart
# required. With an asyncio.Lock, waiters queue on the event loop and cost
# nothing; only the single active request occupies threadpool threads.
_conn: sqlite3.Connection = sqlite3.connect(
    settings.db_uri, uri=True, check_same_thread=False
)
_conn.execute("PRAGMA foreign_keys = ON")
_conn.row_factory = sqlite3.Row
_lock = asyncio.Lock()


async def get_conn() -> AsyncIterator[sqlite3.Connection]:
    """FastAPI dependency. Serialises access to the single in-memory
    connection; commits on clean exit, rolls back on any error or early
    teardown (e.g. client disconnect) so no partial work leaks into the
    next request. Async on purpose — see the outage note above `_lock`.

    RE-ENTRANCY IS FORBIDDEN. `_lock` is a plain asyncio.Lock, NOT re-entrant:
    acquiring it twice from the same call chain DEADLOCKS the whole server
    (every future request blocks forever on the held lock — no crash, no log,
    just a hang). So a request must take a connection exactly once and thread
    it through:

        * In routers, depend on `conn` (ConnDep) and pass it down to db/solver
          helpers — never open a second one inside the handler.
        * Never call `get_conn()` / `conn_ctx()` from code that already holds a
          connection (e.g. inside a db.* or solver.* helper). Those helpers take
          `conn` as a parameter precisely so they don't need to.
    """
    async with _lock:
        try:
            yield _conn
            _conn.commit()
        except BaseException:
            _conn.rollback()
            raise


@contextmanager
def conn_ctx() -> Iterator[sqlite3.Connection]:
    """Sync twin of get_conn for entry points that run with NO server traffic:
    startup `migrate()` (lifespan runs before the first request is accepted)
    and standalone scripts/tests. It deliberately takes no lock — it cannot
    take the request lock (that one lives on the event loop), so using it
    while the server is serving would race the request path. Don't.
    """
    try:
        yield _conn
        _conn.commit()
    except BaseException:
        _conn.rollback()
        raise


def migrate() -> None:
    """Idempotent schema setup. Safe to call on every startup."""
    with conn_ctx() as conn:
        conn.executescript(_SCHEMA)
        # Additive migrations (idempotent).
        room_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(rooms)").fetchall()
        }
        if "name" not in room_cols:
            conn.execute("ALTER TABLE rooms ADD COLUMN name TEXT")
        participant_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(participants)").fetchall()
        }
        if "forced_judge_last" not in participant_cols:
            conn.execute(
                "ALTER TABLE participants "
                "ADD COLUMN forced_judge_last INTEGER NOT NULL DEFAULT 0"
            )


# ─── Token generators ──────────────────────────────────────────────────────
def gen_admin_token() -> str:
    return secrets.token_hex(16)  # 32 hex chars


def gen_browser_token() -> str:
    return secrets.token_hex(16)  # 32 hex chars


def _gen_event_code() -> str:
    return f"{secrets.randbelow(10**9):09d}"


def gen_event_code(conn: sqlite3.Connection, *, max_tries: int = 8) -> str:
    """Generate a 9-digit code that doesn't collide with an existing event."""
    for _ in range(max_tries):
        code = _gen_event_code()
        row = conn.execute(
            "SELECT 1 FROM events WHERE code = ?", (code,)
        ).fetchone()
        if row is None:
            return code
    raise RuntimeError("Could not allocate a unique event code")


# ─── Events ────────────────────────────────────────────────────────────────
def create_event(
    conn: sqlite3.Connection,
    *,
    room_limit: int = 5,
    reg_deadline: Optional[int] = None,
) -> sqlite3.Row:
    """Create an event with a fresh code + admin token. Returns the row."""
    code = gen_event_code(conn)
    admin_token = gen_admin_token()
    now = int(time.time())
    conn.execute(
        "INSERT INTO events(code, admin_token, created_at, reg_deadline, "
        "room_limit, status) VALUES (?,?,?,?,?,?)",
        (code, admin_token, now, reg_deadline, room_limit, "open"),
    )
    return get_event_by_code(conn, code)  # type: ignore[return-value]


def get_event_by_code(
    conn: sqlite3.Connection, code: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM events WHERE code = ?", (code,)
    ).fetchone()


def get_event_by_admin_token(
    conn: sqlite3.Connection, token: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM events WHERE admin_token = ?", (token,)
    ).fetchone()


def update_event_status(
    conn: sqlite3.Connection, code: str, status: str
) -> None:
    conn.execute("UPDATE events SET status = ? WHERE code = ?", (status, code))


def update_event(
    conn: sqlite3.Connection,
    code: str,
    *,
    reg_deadline: Optional[int] = None,
    room_limit: Optional[int] = None,
) -> None:
    """Partial update. None means 'leave alone'."""
    sets, vals = [], []
    if reg_deadline is not None:
        sets.append("reg_deadline = ?")
        vals.append(reg_deadline)
    if room_limit is not None:
        sets.append("room_limit = ?")
        vals.append(room_limit)
    if not sets:
        return
    vals.append(code)
    conn.execute(f"UPDATE events SET {', '.join(sets)} WHERE code = ?", vals)


# ─── Participants ──────────────────────────────────────────────────────────
def insert_participant(
    conn: sqlite3.Connection,
    *,
    event_code: str,
    browser_token: str,
    name: str,
    language: str,
    format: str,
    role: str,
    could_speak_last: bool,
    experience: int,
    special_request: Optional[str] = None,
    forced_judge_last: bool = False,
) -> int:
    """Insert a participant; returns the new id."""
    cur = conn.execute(
        "INSERT INTO participants(event_code, browser_token, name, language, "
        "format, role, could_speak_last, experience, special_request, "
        "forced_judge_last, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            event_code,
            browser_token,
            name,
            language,
            format,
            role,
            1 if could_speak_last else 0,
            int(experience),
            special_request,
            1 if forced_judge_last else 0,
            int(time.time()),
        ),
    )
    return int(cur.lastrowid)


def list_participants(
    conn: sqlite3.Connection, event_code: str
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM participants WHERE event_code = ? ORDER BY id",
        (event_code,),
    ).fetchall()


def count_participants(conn: sqlite3.Connection, event_code: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM participants WHERE event_code = ?",
        (event_code,),
    ).fetchone()
    return int(row["n"]) if row else 0


def get_participant_by_browser_token(
    conn: sqlite3.Connection, event_code: str, browser_token: str
) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM participants WHERE event_code = ? AND browser_token = ?",
        (event_code, browser_token),
    ).fetchone()


def delete_participant_by_browser_token(
    conn: sqlite3.Connection, event_code: str, browser_token: str
) -> None:
    """Remove a participant identified by their browser token (self-service
    unregister). Any slot they sat in becomes empty via ON DELETE SET NULL on
    slots.participant_id — same teardown as the admin-side delete."""
    conn.execute(
        "DELETE FROM participants WHERE event_code = ? AND browser_token = ?",
        (event_code, browser_token),
    )


def update_participant_by_browser_token(
    conn: sqlite3.Connection,
    *,
    event_code: str,
    browser_token: str,
    name: str,
    language: str,
    format: str,
    role: str,
    could_speak_last: bool,
    experience: int,
    special_request: Optional[str] = None,
    forced_judge_last: bool = False,
) -> None:
    """In-place update for a participant identified by their browser token.
    Used by the modify flow. Name collisions (UNIQUE event_code+name) raise
    sqlite3.IntegrityError; the router catches and returns 409."""
    conn.execute(
        "UPDATE participants SET name = ?, language = ?, format = ?, role = ?, "
        "could_speak_last = ?, experience = ?, special_request = ?, "
        "forced_judge_last = ? "
        "WHERE event_code = ? AND browser_token = ?",
        (
            name,
            language,
            format,
            role,
            1 if could_speak_last else 0,
            int(experience),
            special_request,
            1 if forced_judge_last else 0,
            event_code,
            browser_token,
        ),
    )
