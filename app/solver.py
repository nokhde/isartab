"""Room-allocation solver (Google OR-Tools CP-SAT).

Two-phase workflow:
    1. propose_rooms(conn, code)
       Phase 1 — solver decides the *number*, *format* and *language* of
       rooms plus the speaker/judge layout, and writes an **empty** slot
       skeleton to the DB. No participant is placed yet.
    2. (Tabmaster manipulates slots via assign_participant / clear_slot /
        set_slot_locked.)
    3. fill_remaining(conn, code)
       Phase 2 — clears every *unlocked* filled slot (putting those
       participants back into the unplaced pool), then runs a mini-CP that
       fills the open slots. Locked slots are hard pre-assignments.

The solver never opens its own connection and never commits — the caller
owns the transaction (see app.db.conn_ctx).

Participant tuple shape (legacy, kept for the CP code):
    [name, language, format, role, could_speak_last, experience_str]
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from ortools.sat.python import cp_model


# ─── Configuration ─────────────────────────────────────────────────────────
DEFAULT_MAX_ROOMS = 5

#Do not raise this without moving the solve out from under the lock first.
DEFAULT_SOLVE_TIME_LIMIT_S = 10.0
PREFERRED_ROOM_SIZE = (8, 11)
MIN_ROOM_SIZE = 8

MIN_JUDGES = 2
MIN_SPEAKER = 6
HARD_MAX_ROOM_SIZE = 15

# ─── Cost weights ──────────────────────────────────────────────────────────
W_LANG = 300
W_FORMAT = 100
W_DIVERSITY = 450
W_ROLE = 30
W_FORCED_JUDGE = 70
W_OVERSIZE = 20
W_EXP_BEGINNER_JUDGE = 1
W_UNPLACED = 100_000   # Phase 2: penalty if a participant gets no slot
# Was a hard "pure judges can never speak" constraint; now a soft cost so the
# solver can move 2 extra judges into empty BP speaker slots when the room
# is over-judged.
W_PURE_JUDGE_AS_SPEAKER = 35
# Cost per unfilled BP speaker position (OG/OO/CG/CO). Slightly higher than
# W_PURE_JUDGE_AS_SPEAKER so a J→speaker reassignment wins over an empty
# speaker slot in BP rooms.
W_EMPTY_BP_SPEAKER = 50

BP_IDEAL_SPEAKERS = len(["OG", "OG", "OO", "OO", "CG", "CG", "CO", "CO"])  # 8

# ─── Sub-role layouts ──────────────────────────────────────────────────────
BP_SPEAKER_SUBROLES = ["OG", "OG", "OO", "OO", "CG", "CG", "CO", "CO"]
OPD_SPEAKER_SUBROLES = ["Gov", "Gov", "Gov", "Opp", "Opp", "Opp"]
OPD_FREE_SUBROLE = "Free"
# Manually-added (and propose-rooms) OPD rooms always get this many Free
# slots so the tabmaster has somewhere to drop free speakers. If the
# Phase-1 solver assigned more, we extend on the fly (see
# _split_speakers_into_slots).
OPD_FREE_DEFAULT = 3
JUDGE_PANELIST = "Panelist"
JUDGE_OTHER = "Judge"


# ═══════════════════════════════════════════════════════════════════════════
#  Phase-1 solver (pure CP, no DB access)
# ═══════════════════════════════════════════════════════════════════════════
def solve_assignment(
    participants: list,
    *,
    max_rooms: int = DEFAULT_MAX_ROOMS,
    time_limit_s: float = DEFAULT_SOLVE_TIME_LIMIT_S,
) -> dict:
    """Optimal room assignment. Returns {'rooms', 'objective', 'status'}."""
    n = len(participants)
    if n < MIN_ROOM_SIZE:
        return {"rooms": [], "objective": 0, "status": "TOO_FEW_PARTICIPANTS"}

    model = cp_model.CpModel()
    P, R = range(n), range(max_rooms)

    assign = {(p, r): model.NewBoolVar(f"a_{p}_{r}") for p in P for r in R}
    speaker_in = {(p, r): model.NewBoolVar(f"s_{p}_{r}") for p in P for r in R}
    room_used = [model.NewBoolVar(f"u_{r}") for r in R]
    is_opd = [model.NewBoolVar(f"opd_{r}") for r in R]
    is_en = [model.NewBoolVar(f"en_{r}") for r in R]

    for p in P:
        model.AddExactlyOne(assign[p, r] for r in R)
        for r in R:
            model.AddImplication(speaker_in[p, r], assign[p, r])

    # Note: pure judges (role="J") were previously *hard-banned* from
    # speaker positions. We now allow it with a high cost (see cost_terms
    # below) so that an over-judged BP room can shed two judges into the
    # empty OG/OO/CG/CO slots.

    for r in R:
        size = sum(assign[p, r] for p in P)
        speakers = sum(speaker_in[p, r] for p in P)
        model.Add(size >= MIN_ROOM_SIZE).OnlyEnforceIf(room_used[r])
        model.Add(size <= HARD_MAX_ROOM_SIZE).OnlyEnforceIf(room_used[r])
        model.Add(size == 0).OnlyEnforceIf(room_used[r].Not())
        model.Add(speakers >= MIN_SPEAKER).OnlyEnforceIf(room_used[r])
        model.Add(speakers == 0).OnlyEnforceIf(room_used[r].Not())
        model.Add(size - speakers >= MIN_JUDGES).OnlyEnforceIf(room_used[r])

    for r in range(max_rooms - 1):
        model.AddImplication(room_used[r + 1], room_used[r])

    cost_terms: list = []

    def _and_bool(name: str, lits: list) -> Any:
        b = model.NewBoolVar(name)
        model.AddBoolAnd(lits).OnlyEnforceIf(b)
        model.AddBoolOr([lit.Not() for lit in lits]).OnlyEnforceIf(b.Not())
        return b

    for p, part in enumerate(participants):
        _name, lang, fmt, role, could_speak, exp = part
        exp = int(exp)

        if lang in ("DE", "EN"):
            wants_en = lang == "EN"
            for r in R:
                mm = _and_bool(
                    f"lmm_{p}_{r}",
                    [assign[p, r], (is_en[r].Not() if wants_en else is_en[r])],
                )
                cost_terms.append(W_LANG * mm)

        if fmt in ("BP", "OPD"):
            wants_opd = fmt == "OPD"
            for r in R:
                mm = _and_bool(
                    f"fmm_{p}_{r}",
                    [assign[p, r], (is_opd[r].Not() if wants_opd else is_opd[r])],
                )
                cost_terms.append(W_FORMAT * mm)

        speaks_any = sum(speaker_in[p, r] for r in R)
        if role == "S":
            not_speaking = model.NewBoolVar(f"ns_{p}")
            model.Add(speaks_any == 0).OnlyEnforceIf(not_speaking)
            model.Add(speaks_any == 1).OnlyEnforceIf(not_speaking.Not())
            cost_terms.append(W_ROLE * not_speaking)
            if not could_speak:
                cost_terms.append(W_FORCED_JUDGE * not_speaking)

        if exp == 1 and role != "J":
            judging = model.NewBoolVar(f"jd_{p}")
            model.Add(speaks_any == 0).OnlyEnforceIf(judging)
            model.Add(speaks_any == 1).OnlyEnforceIf(judging.Not())
            cost_terms.append(W_EXP_BEGINNER_JUDGE * judging)

        # NEW: pure judges in a speaker chair — soft cost in place of the
        # old hard constraint. Per room because each room costs separately.
        if role == "J":
            for r in R:
                cost_terms.append(W_PURE_JUDGE_AS_SPEAKER * speaker_in[p, r])

    bp_active = [_and_bool(f"bpa_{r}", [room_used[r], is_opd[r].Not()]) for r in R]
    opd_active = [_and_bool(f"oda_{r}", [room_used[r], is_opd[r]]) for r in R]
    has_bp = model.NewBoolVar("has_bp")
    has_opd = model.NewBoolVar("has_opd")
    model.AddMaxEquality(has_bp, bp_active)
    model.AddMaxEquality(has_opd, opd_active)

    num_rooms = sum(room_used)
    multi = model.NewBoolVar("multi")
    model.Add(num_rooms >= 2).OnlyEnforceIf(multi)
    model.Add(num_rooms <= 1).OnlyEnforceIf(multi.Not())
    both = _and_bool("both", [has_bp, has_opd])
    missing_div = _and_bool("miss_div", [multi, both.Not()])
    cost_terms.append(W_DIVERSITY * missing_div)

    for r in R:
        size = sum(assign[p, r] for p in P)
        over = model.NewIntVar(0, HARD_MAX_ROOM_SIZE, f"over_{r}")
        model.AddMaxEquality(over, [size - PREFERRED_ROOM_SIZE[1], 0])
        cost_terms.append(W_OVERSIZE * over)

    # NEW: penalize empty BP speaker positions. Each BP room ideally has
    # BP_IDEAL_SPEAKERS=8 speakers (OG/OG/OO/OO/CG/CG/CO/CO). When this
    # cost outweighs W_PURE_JUDGE_AS_SPEAKER, the solver prefers to move
    # surplus judges into a speaker chair rather than leave it empty.
    for r in R:
        speakers_r = model.NewIntVar(0, HARD_MAX_ROOM_SIZE, f"sc_{r}")
        model.Add(speakers_r == sum(speaker_in[p, r] for p in P))

        # Active when this room is used AND it's a BP room (is_opd[r] = false).
        bp_used = model.NewBoolVar(f"bp_used_{r}")
        model.AddBoolAnd([is_opd[r].Not(), room_used[r]]).OnlyEnforceIf(bp_used)
        model.AddBoolOr([is_opd[r], room_used[r].Not()]).OnlyEnforceIf(bp_used.Not())

        diff_r = model.NewIntVar(-HARD_MAX_ROOM_SIZE, BP_IDEAL_SPEAKERS, f"bpd_{r}")
        model.Add(diff_r == BP_IDEAL_SPEAKERS - speakers_r)
        pos_diff_r = model.NewIntVar(0, BP_IDEAL_SPEAKERS, f"bpdp_{r}")
        model.AddMaxEquality(pos_diff_r, [diff_r, 0])

        bp_under_r = model.NewIntVar(0, BP_IDEAL_SPEAKERS, f"bpu_{r}")
        model.Add(bp_under_r == pos_diff_r).OnlyEnforceIf(bp_used)
        model.Add(bp_under_r == 0).OnlyEnforceIf(bp_used.Not())
        cost_terms.append(W_EMPTY_BP_SPEAKER * bp_under_r)

    model.Minimize(sum(cost_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"rooms": [], "objective": None,
                "status": solver.StatusName(status)}

    rooms = []
    for r in R:
        if not solver.Value(room_used[r]):
            continue
        speakers, judges = [], []
        for p in P:
            if not solver.Value(assign[p, r]):
                continue
            entry = participants[p]
            (speakers if solver.Value(speaker_in[p, r]) else judges).append(entry)
        rooms.append({
            "format": "OPD" if solver.Value(is_opd[r]) else "BP",
            "language": "EN" if solver.Value(is_en[r]) else "DE",
            "speakers": speakers,
            "judges": judges,
        })

    return {"rooms": rooms,
            "objective": solver.ObjectiveValue(),
            "status": solver.StatusName(status)}


# ═══════════════════════════════════════════════════════════════════════════
#  Sub-role allocation (heuristic, runs on the solver output)
# ═══════════════════════════════════════════════════════════════════════════
def _split_speakers_into_slots(fmt: str, speakers: list) -> list[tuple[str, Any]]:
    """Greedy: fill the layout in order; OPD always gets Free-speaker slots."""
    layout = BP_SPEAKER_SUBROLES if fmt == "BP" else OPD_SPEAKER_SUBROLES
    slots = [(sub, speakers[i] if i < len(speakers) else None)
             for i, sub in enumerate(layout)]
    if fmt == "OPD":
        # Solver-assigned overflow beyond Gov/Opp = free speakers.
        overflow = speakers[len(layout):]
        # Even if solver didn't assign anyone, expose the Free slots so the
        # tabmaster can drag people in manually.
        n_free = max(OPD_FREE_DEFAULT, len(overflow))
        for i in range(n_free):
            slots.append((OPD_FREE_SUBROLE,
                          overflow[i] if i < len(overflow) else None))
    return slots


def _split_judges_into_slots(judges: list) -> list[tuple[str, Any]]:
    """Most experienced judge becomes Panelist; the rest are normal Judges."""
    by_exp = sorted(judges, key=lambda j: -int(j[5]))
    return [((JUDGE_PANELIST if i == 0 else JUDGE_OTHER), j)
            for i, j in enumerate(by_exp)]


def _build_room_slots(room: dict) -> list[dict]:
    """Turns {format, language, speakers, judges} into a slot list."""
    slots = []
    for i, (sub, p) in enumerate(_split_speakers_into_slots(room["format"], room["speakers"])):
        slots.append({"role": "speaker", "subrole": sub,
                      "slot_index": i, "participant": p})
    for i, (sub, p) in enumerate(_split_judges_into_slots(room["judges"])):
        slots.append({"role": "judge", "subrole": sub,
                      "slot_index": i, "participant": p})
    return slots


# ═══════════════════════════════════════════════════════════════════════════
#  Validation + helpers
# ═══════════════════════════════════════════════════════════════════════════
def _validate_code(code: str) -> None:
    if not (isinstance(code, str) and len(code) == 9 and code.isdigit()):
        raise ValueError(f"Event code must be 9-digit numeric, was: {code!r}")


def _load_participants(
    conn: sqlite3.Connection, code: str
) -> list[tuple[int, list]]:
    """[(id, [name, lang, fmt, role, could_speak, exp_str]), ...]"""
    rows = conn.execute(
        "SELECT id, name, language, format, role, could_speak_last, experience "
        "FROM participants WHERE event_code = ? ORDER BY id",
        (code,),
    ).fetchall()
    return [(r[0], [r[1], r[2], r[3], r[4], bool(r[5]), str(r[6])]) for r in rows]


def _event_room_limit(conn: sqlite3.Connection, code: str) -> int:
    row = conn.execute(
        "SELECT room_limit FROM events WHERE code = ?", (code,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No event found for code {code}.")
    return int(row[0])


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 1 — propose_rooms
# ═══════════════════════════════════════════════════════════════════════════
def propose_rooms(
    conn: sqlite3.Connection, code: str, *,
    time_limit_s: float = DEFAULT_SOLVE_TIME_LIMIT_S,
) -> dict:
    """Build an empty room skeleton chosen by the solver. **Wipes any
    existing rooms for this event.** No participant is assigned to any slot.
    """
    _validate_code(code)
    max_rooms = _event_room_limit(conn, code)
    parts_with_ids = _load_participants(conn, code)
    if not parts_with_ids:
        raise ValueError(f"No participants in DB for event {code}.")

    participants = [p for _pid, p in parts_with_ids]
    result = solve_assignment(
        participants, max_rooms=max_rooms, time_limit_s=time_limit_s
    )
    if not result["rooms"]:
        return {"rooms": [], "objective": result.get("objective"),
                "status": result.get("status", "NO_ROOMS")}

    conn.execute("DELETE FROM rooms WHERE event_code = ?", (code,))
    for r_idx, room in enumerate(result["rooms"]):
        cur = conn.execute(
            "INSERT INTO rooms(event_code, room_index, format, language) "
            "VALUES (?,?,?,?)",
            (code, r_idx, room["format"], room["language"]),
        )
        room_id = cur.lastrowid
        # Slot skeleton: keep the solver's layout (count + subroles); leave empty.
        for slot in _build_room_slots(room):
            conn.execute(
                "INSERT INTO slots(room_id, role, subrole, slot_index, participant_id, locked) "
                "VALUES (?,?,?,?,NULL,0)",
                (room_id, slot["role"], slot["subrole"], slot["slot_index"]),
            )

    return {**get_rooms(conn, code),
            "objective": result["objective"], "status": result["status"]}


# ═══════════════════════════════════════════════════════════════════════════
#  Read current room/slot state
# ═══════════════════════════════════════════════════════════════════════════
def get_rooms(conn: sqlite3.Connection, code: str) -> dict:
    """Reads the current room/slot assignment from the DB."""
    _validate_code(code)
    rooms = []
    for room_id, idx, name, fmt, lang in conn.execute(
        "SELECT id, room_index, name, format, language FROM rooms "
        "WHERE event_code = ? ORDER BY room_index", (code,)
    ).fetchall():
        slot_rows = conn.execute("""
            SELECT s.id, s.role, s.subrole, s.slot_index, s.locked,
                   p.id, p.name, p.language, p.format, p.role,
                   p.could_speak_last, p.experience, p.special_request,
                   p.forced_judge_last
            FROM slots s LEFT JOIN participants p ON p.id = s.participant_id
            WHERE s.room_id = ?
            ORDER BY CASE s.role WHEN 'speaker' THEN 0 ELSE 1 END, s.slot_index
        """, (room_id,)).fetchall()
        slots = []
        for (sid, srole, sub, si, locked,
             pid, pname, pl, pf, pr, pcs, pex, preq, pfjl) in slot_rows:
            participant = None
            if pid is not None:
                participant = {
                    "id": pid, "name": pname, "language": pl, "format": pf,
                    "role": pr, "could_speak_last": bool(pcs),
                    "experience": str(pex),
                    "special_request": preq,
                    "forced_judge_last": bool(pfjl),
                }
            slots.append({"slot_id": sid, "role": srole, "subrole": sub,
                          "slot_index": si, "locked": bool(locked),
                          "participant": participant})
        rooms.append({"room_id": room_id, "index": idx, "name": name,
                      "format": fmt, "language": lang, "slots": slots})
    return {"rooms": rooms}


# ─── Manual slot manipulation (called by admin UI) ─────────────────────────
def clear_slot(conn: sqlite3.Connection, slot_id: int) -> None:
    # A lock is "pin this person to this slot". When the person leaves,
    # the lock is meaningless — drop it so subsequent fills aren't blocked
    # by a stale lock on an empty slot.
    conn.execute(
        "UPDATE slots SET participant_id = NULL, locked = 0 WHERE id = ?",
        (slot_id,),
    )


def assign_participant(
    conn: sqlite3.Connection, slot_id: int, participant_id: int
) -> None:
    """Pin a person to a slot. If they sat in a different slot, vacate that
    first (and unlock that vacated slot — see clear_slot)."""
    conn.execute(
        "UPDATE slots SET participant_id = NULL, locked = 0 WHERE participant_id = ?",
        (participant_id,),
    )
    conn.execute(
        "UPDATE slots SET participant_id = ? WHERE id = ?",
        (participant_id, slot_id),
    )


def swap_slots(conn: sqlite3.Connection, slot_a: int, slot_b: int) -> None:
    """Exchange the occupants of two slots in a single transaction.

    Whatever each slot holds (a participant or nothing) ends up in the other.
    Both slots are unlocked — a lock pins a *person to a position*, and a swap
    breaks that intent (mirrors assign_participant, which unlocks on move).
    Done in one request so the UI can't get stuck mid-swap.
    """
    a = conn.execute("SELECT participant_id FROM slots WHERE id = ?", (slot_a,)).fetchone()
    b = conn.execute("SELECT participant_id FROM slots WHERE id = ?", (slot_b,)).fetchone()
    pa = a["participant_id"] if a else None
    pb = b["participant_id"] if b else None
    # Clear both first so the UNIQUE(participant_id) index never sees a dup
    # mid-update, then write the exchanged occupants.
    conn.execute(
        "UPDATE slots SET participant_id = NULL, locked = 0 WHERE id IN (?, ?)",
        (slot_a, slot_b),
    )
    conn.execute("UPDATE slots SET participant_id = ? WHERE id = ?", (pb, slot_a))
    conn.execute("UPDATE slots SET participant_id = ? WHERE id = ?", (pa, slot_b))


def set_slot_locked(
    conn: sqlite3.Connection, slot_id: int, locked: bool
) -> None:
    conn.execute(
        "UPDATE slots SET locked = ? WHERE id = ?",
        (1 if locked else 0, slot_id),
    )


def add_room(
    conn: sqlite3.Connection,
    code: str,
    *,
    format: str,
    language: str,
) -> int:
    """Inserts a fresh empty room with the standard slot skeleton.

    Returns the new room_id. Format must be BP or OPD; language DE or EN.
    """
    _validate_code(code)
    if format not in ("BP", "OPD"):
        raise ValueError(f"format must be 'BP' or 'OPD', was {format!r}")
    if language not in ("DE", "EN"):
        raise ValueError(f"language must be 'DE' or 'EN', was {language!r}")

    row = conn.execute(
        "SELECT COALESCE(MAX(room_index), -1) + 1 AS next FROM rooms WHERE event_code = ?",
        (code,),
    ).fetchone()
    next_idx = int(row[0])

    cur = conn.execute(
        "INSERT INTO rooms(event_code, room_index, format, language) "
        "VALUES (?,?,?,?)",
        (code, next_idx, format, language),
    )
    room_id = int(cur.lastrowid)

    # Speaker skeleton: one slot per sub-role for this format.
    speaker_subroles = (
        BP_SPEAKER_SUBROLES if format == "BP" else OPD_SPEAKER_SUBROLES
    )
    for i, sub in enumerate(speaker_subroles):
        conn.execute(
            "INSERT INTO slots(room_id, role, subrole, slot_index, participant_id, locked) "
            "VALUES (?,?,?,?,NULL,0)",
            (room_id, "speaker", sub, i),
        )
    # OPD also gets OPD_FREE_DEFAULT Free Speaker slots — always available
    # for drag-drop, no algorithmic assignment required.
    if format == "OPD":
        for j in range(OPD_FREE_DEFAULT):
            conn.execute(
                "INSERT INTO slots(room_id, role, subrole, slot_index, participant_id, locked) "
                "VALUES (?,?,?,?,NULL,0)",
                (room_id, "speaker", OPD_FREE_SUBROLE,
                 len(speaker_subroles) + j),
            )
    # Judge skeleton: MIN_JUDGES slots (first is Panelist, rest are Judge).
    for i in range(MIN_JUDGES):
        sub = JUDGE_PANELIST if i == 0 else JUDGE_OTHER
        conn.execute(
            "INSERT INTO slots(room_id, role, subrole, slot_index, participant_id, locked) "
            "VALUES (?,?,?,?,NULL,0)",
            (room_id, "judge", sub, i),
        )
    return room_id


def delete_room(conn: sqlite3.Connection, code: str, room_id: int) -> None:
    """Removes a room (and its slots via ON DELETE CASCADE)."""
    _validate_code(code)
    conn.execute(
        "DELETE FROM rooms WHERE id = ? AND event_code = ?",
        (room_id, code),
    )


def add_judge_slot(conn: sqlite3.Connection, room_id: int) -> int:
    """Append one empty judge slot to a room. Returns the new slot_id.

    The first judge slot in a room is the Panelist; everything after is a
    regular Judge. `add_room` only seeds MIN_JUDGES slots and propose_rooms
    seeds exactly as many as the CP put there, so we need this as the
    admin's escape hatch for over-judged rooms.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(slot_index), -1) + 1 AS next "
        "FROM slots WHERE room_id = ? AND role = 'judge'",
        (room_id,),
    ).fetchone()
    next_idx = int(row[0])
    sub = JUDGE_PANELIST if next_idx == 0 else JUDGE_OTHER
    cur = conn.execute(
        "INSERT INTO slots(room_id, role, subrole, slot_index, participant_id, locked) "
        "VALUES (?,?,?,?,NULL,0)",
        (room_id, "judge", sub, next_idx),
    )
    return int(cur.lastrowid)


def add_free_slot(conn: sqlite3.Connection, room_id: int) -> int:
    """Append one empty Free-speaker slot to an OPD room. Returns its slot_id.

    The Gov/Opp chairs are fixed by the format, but the number of free
    speakers is open-ended, so this is the admin's escape hatch — the
    counterpart of `add_judge_slot` for the speaker side.
    """
    row = conn.execute(
        "SELECT COALESCE(MAX(slot_index), -1) + 1 AS next "
        "FROM slots WHERE room_id = ? AND role = 'speaker'",
        (room_id,),
    ).fetchone()
    next_idx = int(row[0])
    cur = conn.execute(
        "INSERT INTO slots(room_id, role, subrole, slot_index, participant_id, locked) "
        "VALUES (?,?,?,?,NULL,0)",
        (room_id, "speaker", OPD_FREE_SUBROLE, next_idx),
    )
    return int(cur.lastrowid)


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 2 — fill_remaining (respects locked)
# ═══════════════════════════════════════════════════════════════════════════
def fill_remaining(
    conn: sqlite3.Connection, code: str, *,
    time_limit_s: float = DEFAULT_SOLVE_TIME_LIMIT_S,
) -> dict:
    """Clears every unlocked filled slot, then fills all open slots with a
    mini-CP. Locked slots stay exactly as they are.
    """
    _validate_code(code)

    # Step 1: vacate every unlocked filled slot.
    conn.execute("""
        UPDATE slots SET participant_id = NULL
        WHERE locked = 0
          AND participant_id IS NOT NULL
          AND room_id IN (SELECT id FROM rooms WHERE event_code = ?)
    """, (code,))

    # Step 2: read fresh state.
    state = get_rooms(conn, code)
    parts_with_ids = _load_participants(conn, code)
    if not state["rooms"]:
        raise ValueError("No rooms exist yet — call propose_rooms() (or add_room()) first.")

    placed = {s["participant"]["id"]
              for room in state["rooms"] for s in room["slots"]
              if s["participant"]}
    unplaced = [(pid, p) for pid, p in parts_with_ids if pid not in placed]

    open_slots: list = []  # (slot_id, room_dict, role, subrole)
    filled: dict = {}      # (room_id, role) -> int
    open_idx_by: dict = {} # (room_id, role) -> list[j]
    for room in state["rooms"]:
        for kind in ("speaker", "judge"):
            filled.setdefault((room["room_id"], kind), 0)
            open_idx_by.setdefault((room["room_id"], kind), [])
        for s in room["slots"]:
            if s["participant"] is None:
                open_idx_by[(room["room_id"], s["role"])].append(len(open_slots))
                open_slots.append((s["slot_id"], room, s["role"], s["subrole"]))
            else:
                filled[(room["room_id"], s["role"])] += 1

    if not unplaced:
        return {**state, "objective": 0, "status": "NOTHING_TO_DO"}

    model = cp_model.CpModel()
    n, m = len(unplaced), len(open_slots)
    x = {(i, j): model.NewBoolVar(f"x_{i}_{j}") for i in range(n) for j in range(m)}

    for i in range(n):
        model.Add(sum(x[i, j] for j in range(m)) <= 1)
    for j in range(m):
        model.Add(sum(x[i, j] for i in range(n)) <= 1)

    # Pure judges in speaker chairs are allowed but costly (see cost_terms);
    # replaces the previous hard constraint so over-judged BP rooms can
    # shed two judges into empty speaker positions instead of leaving them
    # empty.

    # Per-room minimums; pre-locked slots already count toward `filled`.
    for room in state["rooms"]:
        rid = room["room_id"]
        need_spk = max(0, MIN_SPEAKER - filled[(rid, "speaker")])
        need_jdg = max(0, MIN_JUDGES - filled[(rid, "judge")])
        if need_spk:
            model.Add(sum(x[i, j] for i in range(n)
                          for j in open_idx_by[(rid, "speaker")]) >= need_spk)
        if need_jdg:
            model.Add(sum(x[i, j] for i in range(n)
                          for j in open_idx_by[(rid, "judge")]) >= need_jdg)

    cost_terms: list = []
    for i, (_pid, p) in enumerate(unplaced):
        _name, lang, fmt, role, could_speak, exp = p
        for j, (_sid, room, srole, _sub) in enumerate(open_slots):
            if lang in ("DE", "EN") and lang != room["language"]:
                cost_terms.append(W_LANG * x[i, j])
            if fmt in ("BP", "OPD") and fmt != room["format"]:
                cost_terms.append(W_FORMAT * x[i, j])
            if role == "S" and srole == "judge":
                cost_terms.append(W_ROLE * x[i, j])
                if not could_speak:
                    cost_terms.append(W_FORCED_JUDGE * x[i, j])
            if role == "J" and srole == "speaker":
                cost_terms.append(W_PURE_JUDGE_AS_SPEAKER * x[i, j])
            if int(exp) == 1 and srole == "judge":
                cost_terms.append(W_EXP_BEGINNER_JUDGE * x[i, j])

        placed_i = model.NewBoolVar(f"pl_{i}")
        model.Add(sum(x[i, j] for j in range(m)) == 1).OnlyEnforceIf(placed_i)
        model.Add(sum(x[i, j] for j in range(m)) == 0).OnlyEnforceIf(placed_i.Not())
        cost_terms.append(W_UNPLACED * placed_i.Not())

    # NEW: penalty for empty BP speaker positions in phase 2 too. Pushes
    # the solver to fill them even at the cost of W_PURE_JUDGE_AS_SPEAKER.
    for j, (_sid, room, srole, _sub) in enumerate(open_slots):
        if room["format"] == "BP" and srole == "speaker":
            slot_filled = sum(x[i, j] for i in range(n))
            slot_empty = model.NewBoolVar(f"se_{j}")
            model.Add(slot_filled == 0).OnlyEnforceIf(slot_empty)
            model.Add(slot_filled == 1).OnlyEnforceIf(slot_empty.Not())
            cost_terms.append(W_EMPTY_BP_SPEAKER * slot_empty)

    model.Minimize(sum(cost_terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {**state, "objective": None, "status": solver.StatusName(status)}

    for i in range(n):
        for j in range(m):
            if solver.Value(x[i, j]):
                conn.execute(
                    "UPDATE slots SET participant_id = ? WHERE id = ?",
                    (unplaced[i][0], open_slots[j][0]),
                )

    # Post-pass: the CP cost terms above don't distinguish OPD's three
    # speaker sub-roles (Gov / Opp / Free), so it sometimes parks a
    # speaker in a Free slot while Gov/Opp still have empty seats. Fix
    # that deterministically: per OPD room, move people from unlocked
    # Free slots into empty unlocked Gov/Opp slots until those are full.
    _flush_free_speakers_into_gov_opp(conn, code)

    # Fairness post-pass: anyone flagged "forced to judge last time" should
    # not be sat in a judge chair again if a same-room swap is feasible.
    _unswap_forced_judges(conn, code)

    return {**get_rooms(conn, code),
            "objective": solver.ObjectiveValue(),
            "status": solver.StatusName(status)}


def _unswap_forced_judges(conn: sqlite3.Connection, code: str) -> None:
    """For each room, try to swap participants flagged forced_judge_last=True
    out of judge slots into speaker slots.

    Same-room only, so language/format invariants stay intact. Locked slots
    on either side are skipped. Candidate ranking for the speaker we swap
    *into* the judge slot: J/SJ first (judging is fine for them), then S
    only if no other option exists in the room — and never another
    forced-last-time victim.
    """
    rooms = conn.execute(
        "SELECT id FROM rooms WHERE event_code = ?", (code,)
    ).fetchall()
    for (room_id,) in rooms:
        slot_rows = conn.execute(
            "SELECT s.id, s.role, s.locked, s.participant_id, "
            "       p.role, p.forced_judge_last "
            "FROM slots s LEFT JOIN participants p ON p.id = s.participant_id "
            "WHERE s.room_id = ?",
            (room_id,),
        ).fetchall()

        victims: list[tuple[int, int]] = []  # (judge_slot_id, participant_id)
        candidates: list[tuple[int, int, str]] = []  # (speaker_slot_id, pid, role_pref)
        for sid, srole, locked, pid, prole, pfjl in slot_rows:
            if pid is None or locked:
                continue
            if srole == "judge" and pfjl:
                victims.append((sid, pid))
            elif srole == "speaker" and not pfjl:
                candidates.append((sid, pid, prole))

        if not victims or not candidates:
            continue

        # Prefer candidates who already accept judging (J, SJ); fall back to S.
        def rank(c: tuple[int, int, str]) -> int:
            return 0 if c[2] in ("J", "SJ") else 1
        candidates.sort(key=rank)

        for victim_slot, victim_pid in victims:
            if not candidates:
                break
            cand_slot, cand_pid, _ = candidates.pop(0)
            # Swap atomically: clear both, then re-assign crossed.
            conn.execute(
                "UPDATE slots SET participant_id = NULL WHERE id IN (?, ?)",
                (victim_slot, cand_slot),
            )
            conn.execute(
                "UPDATE slots SET participant_id = ? WHERE id = ?",
                (victim_pid, cand_slot),
            )
            conn.execute(
                "UPDATE slots SET participant_id = ? WHERE id = ?",
                (cand_pid, victim_slot),
            )


def _flush_free_speakers_into_gov_opp(
    conn: sqlite3.Connection, code: str
) -> None:
    """For each OPD room: while there's an empty unlocked Gov or Opp slot
    and an occupied unlocked Free-speaker slot, move a participant from
    Free into the missing primary slot. Locked slots are untouched on
    both ends — they're the tabmaster's explicit pins.
    """
    rooms = conn.execute(
        "SELECT id FROM rooms WHERE event_code = ? AND format = 'OPD'",
        (code,),
    ).fetchall()
    for (room_id,) in rooms:
        for primary_sub in ("Gov", "Opp"):
            while True:
                empty_primary = conn.execute(
                    "SELECT id FROM slots "
                    "WHERE room_id = ? AND role = 'speaker' AND subrole = ? "
                    "  AND participant_id IS NULL AND locked = 0 "
                    "ORDER BY slot_index LIMIT 1",
                    (room_id, primary_sub),
                ).fetchone()
                if empty_primary is None:
                    break
                free_filled = conn.execute(
                    "SELECT id, participant_id FROM slots "
                    "WHERE room_id = ? AND role = 'speaker' AND subrole = ? "
                    "  AND participant_id IS NOT NULL AND locked = 0 "
                    "ORDER BY slot_index LIMIT 1",
                    (room_id, OPD_FREE_SUBROLE),
                ).fetchone()
                if free_filled is None:
                    break
                # Vacate the Free slot, then drop the participant into
                # the primary slot. Two updates, not assign_participant,
                # so we don't touch any unrelated slots / locks.
                conn.execute(
                    "UPDATE slots SET participant_id = NULL WHERE id = ?",
                    (free_filled[0],),
                )
                conn.execute(
                    "UPDATE slots SET participant_id = ? WHERE id = ?",
                    (free_filled[1], empty_primary[0]),
                )


# ═══════════════════════════════════════════════════════════════════════════
#  Pretty printing (used by the smoke test / demo)
# ═══════════════════════════════════════════════════════════════════════════
def print_rooms(state: dict) -> None:
    if "status" in state:
        print(f"Status: {state.get('status')} | Penalty: {state.get('objective')}")
    for room in state["rooms"]:
        print(f"\n── Room {room['index'] + 1} | {room['language']} | "
              f"{room['format']} ──")
        for s in room["slots"]:
            p = s["participant"]
            lock = " 🔒" if s.get("locked") else ""
            if p:
                tag = (f"{p['name']:18s}  pref: {p['language']}/{p['format']}/"
                       f"{p['role']} (Exp {p['experience']})")
            else:
                tag = "<empty>"
            print(f"  [{s['role']:7s} {s['subrole']:9s}]{lock} {tag}")
