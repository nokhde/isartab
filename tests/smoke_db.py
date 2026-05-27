"""Chunk 2 smoke test.

Runs the full DAO surface against a fresh SQLite file and asserts the
proposal §8 Chunk-2 'done when' criterion: a script can create an event,
insert participants, and list them through the DAO.

Usage:
    DATA_DIR=./data .venv/bin/python tests/smoke_db.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Run against an isolated DB so the dev one isn't touched.
REPO = Path(__file__).resolve().parents[1]
SMOKE_DIR = REPO / "data" / "smoke"
SMOKE_DIR.mkdir(parents=True, exist_ok=True)
os.environ["DATA_DIR"] = str(SMOKE_DIR)
sys.path.insert(0, str(REPO))

# Drop any leftover DB from a previous run so we exercise migrate() cleanly.
db_file = SMOKE_DIR / "tournaments.db"
for p in SMOKE_DIR.glob("tournaments.db*"):
    p.unlink()

from app import db  # noqa: E402

db.migrate()
print(f"migrate() ok → {db_file}")

with db.conn_ctx() as conn:
    event = db.create_event(conn, room_limit=4)
    code = event["code"]
    admin_token = event["admin_token"]
    print(f"created event code={code} admin_token={admin_token[:8]}…")
    assert len(code) == 9 and code.isdigit()
    assert len(admin_token) == 32 and event["status"] == "open"
    assert event["room_limit"] == 4

    # Look up both ways.
    assert db.get_event_by_code(conn, code)["admin_token"] == admin_token
    assert db.get_event_by_admin_token(conn, admin_token)["code"] == code
    assert db.get_event_by_code(conn, "999999999") is None

    # Insert a handful of participants.
    sample = [
        ("Anna",  "DE",    "BP",  "S",  True,  2),
        ("Bilal", "EN",    "BP",  "J",  True,  3),
        ("Cleo",  "DE/EN", "OPD", "SJ", False, 1),
        ("Dario", "EN",    "egal","S",  True,  2),
    ]
    ids = []
    for name, lang, fmt, role, csl, exp in sample:
        pid = db.insert_participant(
            conn,
            event_code=code,
            browser_token=db.gen_browser_token(),
            name=name,
            language=lang,
            format=fmt,
            role=role,
            could_speak_last=csl,
            experience=exp,
            special_request="bring water" if name == "Cleo" else None,
        )
        ids.append(pid)
    print(f"inserted {len(ids)} participants → ids {ids}")

    listed = db.list_participants(conn, code)
    assert [r["name"] for r in listed] == [s[0] for s in sample]
    assert db.count_participants(conn, code) == 4
    assert listed[2]["special_request"] == "bring water"
    assert listed[2]["could_speak_last"] == 0  # False persisted as 0

    # Browser-token lookup.
    btok = listed[1]["browser_token"]
    assert db.get_participant_by_browser_token(conn, code, btok)["name"] == "Bilal"

    # Event updates.
    db.update_event_status(conn, code, "closed")
    db.update_event(conn, code, reg_deadline=1_700_000_000, room_limit=6)
    refreshed = db.get_event_by_code(conn, code)
    assert refreshed["status"] == "closed"
    assert refreshed["reg_deadline"] == 1_700_000_000
    assert refreshed["room_limit"] == 6

    # Duplicate-name guard.
    try:
        db.insert_participant(
            conn,
            event_code=code,
            browser_token=db.gen_browser_token(),
            name="Anna", language="DE", format="BP", role="S",
            could_speak_last=True, experience=2,
        )
    except Exception as e:
        print(f"duplicate-name correctly rejected: {type(e).__name__}")
    else:
        raise AssertionError("expected UNIQUE(event_code, name) to reject duplicate")

print("\nALL CHECKS PASSED")
