"""Regression test for the 2026-07-01 threadpool-starvation outage.

FastAPI runs *sync* yield-dependencies through the anyio threadpool (capped
at 40 tokens). When get_conn was a sync generator guarding the single SQLite
connection with a threading lock, every request waiting for the lock pinned
one token for its whole wait. The waiting page polls /public once per second
per open tab, so a single ~10 s solver run under the lock let 40+ polls pile
up and pin every token — and the lock-holder then needed one more token to
run its own handler. Circular wait: total, silent, permanent deadlock (no
exception, no log line, /healthz starved, container unhealthy).

The fix: get_conn is an ASYNC generator and the lock an asyncio.Lock, so
waiters queue on the event loop and consume no threads. This test guards:

    1. get_conn stays an async generator with an asyncio.Lock (the outage
       comes back the moment either reverts to sync/threading).
    2. Enter/teardown works and frees the lock; errors roll back and free it.
    3. Many concurrent waiters while the lock is held spawn/pin no threads.

Usage:
    .venv/bin/python tests/smoke_concurrency.py
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app import db  # noqa: E402

db.migrate()

# 1. The shape itself is the fix — assert it directly.
assert inspect.isasyncgenfunction(db.get_conn), (
    "get_conn must be an async generator — a sync yield-dependency waits for "
    "the DB lock inside an anyio threadpool token (regression of the "
    "2026-07-01 deadlock outage)"
)
assert isinstance(db._lock, asyncio.Lock), (
    "the DB lock must be an asyncio.Lock awaited on the event loop, not a "
    "threading primitive"
)
print("get_conn is async + asyncio.Lock ok")


async def _drain(gen) -> None:
    """Enter and cleanly tear down one get_conn generator."""
    await gen.__anext__()
    try:
        await gen.__anext__()
    except StopAsyncIteration:
        pass


async def main() -> None:
    # 2a. Normal path: enter holds the lock, teardown commits and frees it.
    gen = db.get_conn()
    conn = await gen.__anext__()
    assert conn is db._conn
    assert db._lock.locked(), "lock must be held between enter and teardown"
    try:
        await gen.__anext__()
    except StopAsyncIteration:
        pass
    assert not db._lock.locked(), "lock still held after teardown — wedged"
    print("enter/teardown releases lock ok")

    # 2b. Error path: the write is rolled back and the lock freed.
    gen = db.get_conn()
    conn = await gen.__anext__()
    event = db.create_event(conn)
    code = event["code"]
    try:
        await gen.athrow(RuntimeError("boom"))
    except RuntimeError:
        pass
    assert not db._lock.locked(), "lock still held after error teardown"
    with db.conn_ctx() as c:
        assert db.get_event_by_code(c, code) is None, (
            "write survived an aborted request — rollback on error is broken"
        )
    print("error path rolls back + releases lock ok")

    # 3. Pile-up: 100 waiters while the lock is held must not touch a single
    #    thread. This is the load pattern of the outage (waiting-page polls
    #    queueing behind a long solver run).
    gen = db.get_conn()
    await gen.__anext__()  # hold the lock
    threads_before = threading.active_count()
    waiters = [asyncio.ensure_future(_drain(db.get_conn())) for _ in range(100)]
    await asyncio.sleep(0.1)  # let every waiter reach the lock
    threads_during = threading.active_count()
    assert threads_during == threads_before, (
        f"{threads_during - threads_before} thread(s) spawned by lock waiters "
        "— waiting must be free or the threadpool starves again"
    )
    try:
        await gen.__anext__()  # release; waiters drain one by one
    except StopAsyncIteration:
        pass
    await asyncio.gather(*waiters)
    assert not db._lock.locked()
    print("100 queued waiters used 0 threads ok")


asyncio.run(main())
print("\nsmoke_concurrency: PASS")
