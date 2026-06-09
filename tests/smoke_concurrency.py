"""Regression test for the 'cannot release un-acquired lock' outage.

get_conn() acquires the DB lock before `yield` and releases it on teardown.
FastAPI runs sync yield-dependencies in the anyio threadpool and does NOT
guarantee the pre- and post-yield halves run on the same worker thread. A
thread-owned lock (threading.Lock/RLock) released from a different thread
raises RuntimeError, stays held forever, and deadlocks the whole server.

This test drives a get_conn() generator: enter on the main thread, run the
teardown on a *different* thread (as the threadpool would). It must complete
cleanly and leave the lock free.

Usage:
    .venv/bin/python tests/smoke_concurrency.py
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app import db  # noqa: E402

db.migrate()

# Enter the dependency on the main thread (acquires the lock here). We must NOT
# touch the lock again before teardown — it is intentionally non-reentrant.
gen = db.get_conn()
conn = next(gen)
assert conn is db._conn

# Run the teardown on a different thread, as the anyio threadpool may.
captured: dict[str, BaseException] = {}


def teardown() -> None:
    try:
        next(gen)  # resume past yield: commit + release the lock
    except StopIteration:
        pass  # normal: generator finished cleanly
    except BaseException as exc:  # noqa: BLE001
        captured["exc"] = exc


t = threading.Thread(target=teardown)
t.start()
t.join()

assert "exc" not in captured, (
    f"cross-thread teardown raised {captured['exc']!r} "
    "— lock is thread-owned again (regression of the outage bug)"
)
print("cross-thread teardown ok (no RuntimeError)")

# The lock must be free: a held lock means every future request deadlocks.
acquired = db._lock.acquire(blocking=False)
assert acquired, "lock still held after teardown — server would be wedged"
db._lock.release()
print("lock released after teardown ok")

print("\nsmoke_concurrency: PASS")
