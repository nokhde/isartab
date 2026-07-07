"""Crash-attempt suite for the fixed server.

Recreates the production outage conditions, harder, and tries the new
failure modes the async-lock design could introduce:

  Phase A: 3 waves of 200 simultaneous /public polls (old code died at 1x150).
  Phase B: 40 registrations racing the polls (concurrent writes + reads).
  Phase C: solver runs (propose-rooms, magic-fill) while 60 tabs poll at 1 Hz
           AND 50 clients with 0.15 s timeouts abort while queued on the lock
           (cancellation path — a leaked lock here wedges everything).
  Phase D: another 200-burst after all that, then a data-consistency check.

A watchdog probes /healthz every 0.5 s the entire time. Any probe failure,
any hung thread, or inconsistent final state = FAIL.
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.request

BASE = sys.argv[1]
failures: list[str] = []
flock = threading.Lock()


def fail(msg: str) -> None:
    with flock:
        failures.append(msg)


def req(path, timeout=20.0, method="GET", body=None):
    r = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return resp.status, resp.read()


# ── setup: create event ────────────────────────────────────────────────────
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None

opener = urllib.request.build_opener(NoRedirect)
try:
    resp = opener.open(urllib.request.Request(BASE + "/events", method="POST"), timeout=5)
    admin_url = resp.headers.get("Location", "")
except urllib.error.HTTPError as e:
    admin_url = e.headers.get("Location", "")
TOK = admin_url.rstrip("/").split("/")[-1]
CODE = json.loads(req(f"/api/admin/{TOK}/state")[1])["event"]["code"]
print(f"event {CODE}")

# ── watchdog: /healthz every 0.5 s for the whole run ───────────────────────
stop = threading.Event()
health_ok = [0]

def watchdog():
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            req("/healthz", timeout=5)
            health_ok[0] += 1
        except Exception as e:
            fail(f"healthz failed mid-run: {e!r}")
        time.sleep(max(0.0, 0.5 - (time.monotonic() - t0)))

wd = threading.Thread(target=watchdog, daemon=True)
wd.start()


def run_all(threads, timeout=60):
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    for t in threads:
        t.join(timeout=max(0.1, deadline - time.monotonic()))
    hung = sum(1 for t in threads if t.is_alive())
    if hung:
        fail(f"{hung} client threads hung > {timeout}s")
    return hung == 0


# ── Phase A: 3 waves of 200 simultaneous polls ─────────────────────────────
for wave in range(3):
    barrier = threading.Barrier(201)
    ok = [0]
    def poll(b=barrier, ok=ok):
        b.wait()
        try:
            req(f"/api/events/{CODE}/public", timeout=20)
            with flock:
                ok[0] += 1
        except Exception as e:
            fail(f"A poll: {e!r}")
    ts = [threading.Thread(target=poll, daemon=True) for _ in range(200)]
    for t in ts:
        t.start()
    barrier.wait()
    run_all([], 0)
    for t in ts:
        t.join(timeout=30)
    if any(t.is_alive() for t in ts):
        fail(f"A wave {wave}: hung threads")
    print(f"A wave {wave + 1}: {ok[0]}/200 polls ok")

# ── Phase B: 40 registrations racing 100 polls ─────────────────────────────
def register(i):
    body = {"browser_token": f"{i:032d}", "name": f"P{i}", "language": "DE",
            "format": "BP", "role": "SJ", "could_speak_last": True,
            "experience": (i % 3) + 1}
    try:
        s, _ = req(f"/api/events/{CODE}/participants", method="POST", body=body)
        if s != 201:
            fail(f"B register {i}: HTTP {s}")
    except Exception as e:
        fail(f"B register {i}: {e!r}")

ts = [threading.Thread(target=register, args=(i,), daemon=True) for i in range(40)]
ts += [threading.Thread(target=lambda: req(f"/api/events/{CODE}/public"), daemon=True)
       for _ in range(100)]
run_all(ts)
n = json.loads(req(f"/api/events/{CODE}/public")[1])["participant_count"]
print(f"B: {n}/40 registered under contention")
if n != 40:
    fail(f"B: expected 40 participants, got {n}")

# ── Phase C: solver under fire + aborting clients ──────────────────────────
req(f"/api/admin/{TOK}/close-registration", method="POST", body={})
print("C: registration closed")

c_stop = threading.Event()

def polling_tab():
    while not c_stop.is_set():
        try:
            req(f"/api/events/{CODE}/public", timeout=30)
        except Exception as e:
            fail(f"C tab poll: {e!r}")
        time.sleep(1)

def aborter():
    # client that gives up almost immediately — cancels while queued
    while not c_stop.is_set():
        try:
            req(f"/api/events/{CODE}/public", timeout=0.15)
        except Exception:
            pass  # timeouts are the point
        time.sleep(0.2)

tabs = [threading.Thread(target=polling_tab, daemon=True) for _ in range(60)]
aborters = [threading.Thread(target=aborter, daemon=True) for _ in range(50)]
for t in tabs + aborters:
    t.start()
time.sleep(1)

t0 = time.monotonic()
s, b = req(f"/api/admin/{TOK}/propose-rooms", method="POST", body={}, timeout=60)
rooms = len(json.loads(b)["rooms"])
print(f"C: propose-rooms ok ({time.monotonic() - t0:.1f}s, {rooms} rooms, HTTP {s})")
t0 = time.monotonic()
s, _ = req(f"/api/admin/{TOK}/magic-fill", method="POST", body={}, timeout=60)
print(f"C: magic-fill ok ({time.monotonic() - t0:.1f}s, HTTP {s})")

time.sleep(3)  # let tabs+aborters keep hammering post-solve
c_stop.set()
for t in tabs + aborters:
    t.join(timeout=35)
if any(t.is_alive() for t in tabs + aborters):
    fail("C: hung tab/aborter threads")
print("C: 60 tabs + 50 aborting clients survived the solver runs")

# ── Phase D: final burst + consistency check ───────────────────────────────
barrier = threading.Barrier(201)
ok = [0]
def poll_final():
    barrier.wait()
    try:
        req(f"/api/events/{CODE}/public", timeout=20)
        with flock:
            ok[0] += 1
    except Exception as e:
        fail(f"D poll: {e!r}")
ts = [threading.Thread(target=poll_final, daemon=True) for _ in range(200)]
for t in ts:
    t.start()
barrier.wait()
for t in ts:
    t.join(timeout=30)
print(f"D: final burst {ok[0]}/200 ok")

req(f"/api/admin/{TOK}/publish", method="POST", body={})
state = json.loads(req(f"/api/admin/{TOK}/state")[1])
placed = sum(1 for r in state["rooms"] for sl in r["slots"] if sl["participant"])
pids = [sl["participant"]["id"] for r in state["rooms"] for sl in r["slots"] if sl["participant"]]
if len(pids) != len(set(pids)):
    fail("D: participant seated in two slots — transaction corruption")
me = json.loads(req(f"/api/events/{CODE}/participants/me?token={3:032d}")[1])
print(f"D: published, {placed} seated, no duplicates, /me ok "
      f"(P3 slot={bool(me['slot'])})")

stop.set()
wd.join(timeout=6)
print(f"\nwatchdog: {health_ok[0]} healthz probes succeeded, "
      f"{sum('healthz' in f for f in failures)} failed")

if failures:
    print(f"\nFAIL ({len(failures)}):")
    for f in failures[:20]:
        print("  -", f)
    sys.exit(1)
print("\nstress_crash: PASS — server never stopped responding")
