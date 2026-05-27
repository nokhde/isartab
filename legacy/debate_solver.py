"""Debattier-Zuteilung via Google OR-Tools CP-SAT, mit SQLite-Persistenz.

Workflow:
    1. store_tournament(conn, code, participants)  — Teilnehmer in DB ablegen.
    2. propose_rooms(conn, code)                   — Phase 1: Solver erstellt
       Vorschlag mit Sub-Rollen (OG/OO/CG/CO bzw. Gov/Opp/Free, Panelist/Judge)
       und schreibt ihn in die DB.
    3. (Tabmaster bearbeitet Slots manuell via clear_slot / assign_participant.)
    4. fill_remaining(conn, code)                  — Phase 2: füllt offene Slots
       mit noch nicht platzierten Teilnehmern auf (manuelle Vorbelegungen sind
       harte Constraints).

Eingabe-Schema je Teilnehmer:
    [Name, Sprache, Format, Rolle, konnte_letztes_mal_sprechen, Erfahrung]
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ortools.sat.python import cp_model


# ─── Konfiguration ──────────────────────────────────────────────────────────
MAX_ROOMS = 5
PREFERRED_ROOM_SIZE = (8, 11)
MIN_ROOM_SIZE = 8

# Mindestanforderungen pro Raum (gelten unabhängig vom Format).
# MIN_JUDGES + MIN_SPEAKER = MIN_ROOM_SIZE per Konvention.
MIN_JUDGES = 2
MIN_SPEAKER = 6

HARD_MAX_ROOM_SIZE = 15

# ─── Gewichte der Kostenfunktion ────────────────────────────────────────────
W_LANG = 300
W_FORMAT = 100
W_DIVERSITY = 450
W_ROLE = 30
W_FORCED_JUDGE = 70
W_OVERSIZE = 20
W_EXP_BEGINNER_JUDGE = 1
W_UNPLACED = 100_000   # Phase 2: Strafe, falls jemand keinen Slot bekommt

# ─── Sub-Rollen-Layouts ─────────────────────────────────────────────────────
# Reihenfolge bestimmt, wie der Solver-Output greedy in Slots gefüllt wird.
BP_SPEAKER_SUBROLES = ["OG", "OG", "OO", "OO", "CG", "CG", "CO", "CO"]
OPD_SPEAKER_SUBROLES = ["Gov", "Gov", "Gov", "Opp", "Opp", "Opp"]
OPD_FREE_SUBROLE = "Free"
JUDGE_PANELIST = "Panelist"
JUDGE_OTHER = "Judge"

DB_PATH = "tournaments.db"


# ═══════════════════════════════════════════════════════════════════════════
#  Phase-1-Solver (reine CP-Logik, kein DB-Zugriff)
# ═══════════════════════════════════════════════════════════════════════════
def solve_assignment(participants, time_limit_s: float = 30.0):
    """Optimale Raum-Zuteilung. Gibt rooms (mit speakers/judges-Listen) zurück."""
    n = len(participants)
    if n < MIN_ROOM_SIZE:
        return {"rooms": [], "objective": 0, "status": "TOO_FEW_PARTICIPANTS"}

    model = cp_model.CpModel()
    P, R = range(n), range(MAX_ROOMS)

    assign = {(p, r): model.NewBoolVar(f"a_{p}_{r}") for p in P for r in R}
    speaker_in = {(p, r): model.NewBoolVar(f"s_{p}_{r}") for p in P for r in R}
    room_used = [model.NewBoolVar(f"u_{r}") for r in R]
    is_opd = [model.NewBoolVar(f"opd_{r}") for r in R]
    is_en = [model.NewBoolVar(f"en_{r}") for r in R]

    for p in P:
        model.AddExactlyOne(assign[p, r] for r in R)
        for r in R:
            model.AddImplication(speaker_in[p, r], assign[p, r])

    for p, part in enumerate(participants):
        if part[3] == "J":
            for r in R:
                model.Add(speaker_in[p, r] == 0)

    for r in R:
        size = sum(assign[p, r] for p in P)
        speakers = sum(speaker_in[p, r] for p in P)
        model.Add(size >= MIN_ROOM_SIZE).OnlyEnforceIf(room_used[r])
        model.Add(size <= HARD_MAX_ROOM_SIZE).OnlyEnforceIf(room_used[r])
        model.Add(size == 0).OnlyEnforceIf(room_used[r].Not())
        model.Add(speakers >= MIN_SPEAKER).OnlyEnforceIf(room_used[r])
        model.Add(speakers == 0).OnlyEnforceIf(room_used[r].Not())
        model.Add(size - speakers >= MIN_JUDGES).OnlyEnforceIf(room_used[r])

    for r in range(MAX_ROOMS - 1):
        model.AddImplication(room_used[r + 1], room_used[r])

    cost_terms = []

    def _and_bool(name, lits):
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
#  Sub-Rollen-Verteilung (Heuristik, läuft post-hoc auf Solver-Output)
# ═══════════════════════════════════════════════════════════════════════════
def _split_speakers_into_slots(fmt: str, speakers: list) -> list[tuple[str, Any]]:
    """Verteilt Speaker greedy auf Sub-Rollen-Slots.

    Bei OPD: Überlauf > 6 wird zu Free-Speakern.
    Bei BP: über 8 Speaker werden ignoriert (Solver erlaubt das ohnehin nicht).
    Rückgabe: Liste von (subrole, participant_or_None) — gleicher Länge wie
    erwartete Slots (oder länger bei OPD-Free-Speakern).
    """
    layout = BP_SPEAKER_SUBROLES if fmt == "BP" else OPD_SPEAKER_SUBROLES
    slots = [(sub, speakers[i] if i < len(speakers) else None)
             for i, sub in enumerate(layout)]
    if fmt == "OPD" and len(speakers) > len(layout):
        slots.extend((OPD_FREE_SUBROLE, p) for p in speakers[len(layout):])
    return slots


def _split_judges_into_slots(judges: list) -> list[tuple[str, Any]]:
    """Erfahrenster Judge wird Panelist, Rest sind normale Judges."""
    by_exp = sorted(judges, key=lambda j: -int(j[5]))
    return [((JUDGE_PANELIST if i == 0 else JUDGE_OTHER), j)
            for i, j in enumerate(by_exp)]


def _build_room_slots(room: dict) -> list[dict]:
    """Wandelt {format, language, speakers, judges} in eine Slot-Liste um."""
    slots = []
    for i, (sub, p) in enumerate(_split_speakers_into_slots(room["format"], room["speakers"])):
        slots.append({"role": "speaker", "subrole": sub,
                      "slot_index": i, "participant": p})
    for i, (sub, p) in enumerate(_split_judges_into_slots(room["judges"])):
        slots.append({"role": "judge", "subrole": sub,
                      "slot_index": i, "participant": p})
    return slots


# ═══════════════════════════════════════════════════════════════════════════
#  SQLite-Layer
# ═══════════════════════════════════════════════════════════════════════════
_SCHEMA = """
CREATE TABLE IF NOT EXISTS tournaments (
    code TEXT PRIMARY KEY
        CHECK (length(code) = 9 AND code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]')
);
CREATE TABLE IF NOT EXISTS participants (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_code   TEXT NOT NULL REFERENCES tournaments(code) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    language          TEXT NOT NULL,
    format            TEXT NOT NULL,
    role              TEXT NOT NULL,
    could_speak_last  INTEGER NOT NULL,
    experience        INTEGER NOT NULL,
    UNIQUE (tournament_code, name)
);
CREATE TABLE IF NOT EXISTS rooms (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_code   TEXT NOT NULL REFERENCES tournaments(code) ON DELETE CASCADE,
    room_index        INTEGER NOT NULL,
    format            TEXT NOT NULL CHECK (format IN ('BP','OPD')),
    language          TEXT NOT NULL CHECK (language IN ('DE','EN')),
    UNIQUE (tournament_code, room_index)
);
CREATE TABLE IF NOT EXISTS slots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id         INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('speaker','judge')),
    subrole         TEXT NOT NULL,
    slot_index      INTEGER NOT NULL,
    participant_id  INTEGER REFERENCES participants(id) ON DELETE SET NULL,
    -- Eine Person darf höchstens einen Slot belegen (NULL ist mehrfach erlaubt).
    UNIQUE (participant_id)
);
"""


def init_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Öffnet die DB und legt das Schema an (idempotent)."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _validate_code(code: str) -> None:
    if not (isinstance(code, str) and len(code) == 9 and code.isdigit()):
        raise ValueError(f"Turniercode muss 9-stellig numerisch sein, war: {code!r}")


def store_tournament(conn: sqlite3.Connection, code: str, participants: list) -> None:
    """Legt/überschreibt Teilnehmer für ein Turnier. Räume werden zurückgesetzt."""
    _validate_code(code)
    conn.execute("INSERT OR IGNORE INTO tournaments(code) VALUES (?)", (code,))
    conn.execute("DELETE FROM rooms WHERE tournament_code = ?", (code,))
    conn.execute("DELETE FROM participants WHERE tournament_code = ?", (code,))
    for part in participants:
        name, lang, fmt, role, could_speak, exp = part
        conn.execute(
            "INSERT INTO participants(tournament_code, name, language, format, "
            "role, could_speak_last, experience) VALUES (?,?,?,?,?,?,?)",
            (code, name, lang, fmt, role, int(bool(could_speak)), int(exp)),
        )
    conn.commit()


def _load_participants(conn: sqlite3.Connection, code: str) -> list[tuple[int, list]]:
    """[(id, [name, lang, fmt, role, could_speak, exp_str]), ...]"""
    rows = conn.execute(
        "SELECT id, name, language, format, role, could_speak_last, experience "
        "FROM participants WHERE tournament_code = ? ORDER BY id",
        (code,),
    ).fetchall()
    return [(r[0], [r[1], r[2], r[3], r[4], bool(r[5]), str(r[6])]) for r in rows]


def propose_rooms(conn: sqlite3.Connection, code: str,
                  time_limit_s: float = 30.0) -> dict:
    """Phase 1: legt die leeren Raum-Gerüste an.

    Der Solver wird genutzt, um Anzahl/Format/Sprache der Räume und die
    Aufteilung in Speaker-/Judge-Slots zu bestimmen — aber **keine** Personen
    werden platziert. Alle Slots werden leer (participant_id = NULL) gespeichert.
    Der Tabmaster kann dann im UI gezielt Personen in einzelne Slots ziehen;
    fill_remaining() füllt anschließend den Rest optimal auf.
    """
    _validate_code(code)
    parts_with_ids = _load_participants(conn, code)
    if not parts_with_ids:
        raise ValueError(f"Keine Teilnehmer in DB für Turnier {code}.")

    participants = [p for _pid, p in parts_with_ids]
    result = solve_assignment(participants, time_limit_s=time_limit_s)
    if not result["rooms"]:
        return {"rooms": [], "objective": result.get("objective"),
                "status": result.get("status", "NO_ROOMS")}

    conn.execute("DELETE FROM rooms WHERE tournament_code = ?", (code,))
    for r_idx, room in enumerate(result["rooms"]):
        cur = conn.execute(
            "INSERT INTO rooms(tournament_code, room_index, format, language) "
            "VALUES (?,?,?,?)",
            (code, r_idx, room["format"], room["language"]),
        )
        room_id = cur.lastrowid
        # Slot-Gerüst aus dem Solver-Layout übernehmen (Anzahl & Sub-Rollen),
        # aber alle Plätze leer lassen.
        for slot in _build_room_slots(room):
            conn.execute(
                "INSERT INTO slots(room_id, role, subrole, slot_index, participant_id) "
                "VALUES (?,?,?,?,NULL)",
                (room_id, slot["role"], slot["subrole"], slot["slot_index"]),
            )
    conn.commit()

    return {**get_rooms(conn, code),
            "objective": result["objective"], "status": result["status"]}


def get_rooms(conn: sqlite3.Connection, code: str) -> dict:
    """Liest die aktuelle Raum-/Slot-Belegung aus der DB."""
    _validate_code(code)
    rooms = []
    for room_id, idx, fmt, lang in conn.execute(
        "SELECT id, room_index, format, language FROM rooms "
        "WHERE tournament_code = ? ORDER BY room_index", (code,)
    ).fetchall():
        slot_rows = conn.execute("""
            SELECT s.id, s.role, s.subrole, s.slot_index,
                   p.id, p.name, p.language, p.format, p.role,
                   p.could_speak_last, p.experience
            FROM slots s LEFT JOIN participants p ON p.id = s.participant_id
            WHERE s.room_id = ?
            ORDER BY CASE s.role WHEN 'speaker' THEN 0 ELSE 1 END, s.slot_index
        """, (room_id,)).fetchall()
        slots = []
        for sid, srole, sub, si, pid, pname, pl, pf, pr, pcs, pex in slot_rows:
            participant = None
            if pid is not None:
                participant = {
                    "id": pid, "name": pname, "language": pl, "format": pf,
                    "role": pr, "could_speak_last": bool(pcs),
                    "experience": str(pex),
                }
            slots.append({"slot_id": sid, "role": srole, "subrole": sub,
                          "slot_index": si, "participant": participant})
        rooms.append({"room_id": room_id, "index": idx, "format": fmt,
                      "language": lang, "slots": slots})
    return {"rooms": rooms}


# ─── Manuelle Slot-Manipulation (vom UI aufgerufen) ─────────────────────────
def clear_slot(conn: sqlite3.Connection, slot_id: int) -> None:
    conn.execute("UPDATE slots SET participant_id = NULL WHERE id = ?", (slot_id,))
    conn.commit()


def assign_participant(conn: sqlite3.Connection, slot_id: int,
                       participant_id: int) -> None:
    """Setzt eine Person in einen Slot und entfernt sie aus ihrem alten Slot."""
    conn.execute("UPDATE slots SET participant_id = NULL WHERE participant_id = ?",
                 (participant_id,))
    conn.execute("UPDATE slots SET participant_id = ? WHERE id = ?",
                 (participant_id, slot_id))
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 2: offene Slots mit nicht-platzierten Teilnehmern auffüllen
# ═══════════════════════════════════════════════════════════════════════════
def fill_remaining(conn: sqlite3.Connection, code: str,
                   time_limit_s: float = 30.0) -> dict:
    """Phase 2: Mini-CP, das offene Slots mit unplatzierten Personen füllt.

    Räume (Format/Sprache) und bereits gefüllte Slots bleiben unangetastet.
    """
    _validate_code(code)
    state = get_rooms(conn, code)
    parts_with_ids = _load_participants(conn, code)
    if not state["rooms"]:
        raise ValueError("Keine Räume vorhanden — propose_rooms() zuerst aufrufen.")

    placed = {s["participant"]["id"]
              for room in state["rooms"] for s in room["slots"]
              if s["participant"]}
    unplaced = [(pid, p) for pid, p in parts_with_ids if pid not in placed]

    open_slots = []  # (slot_id, room_dict, role)
    # Pro Raum bereits gefüllte Slots zählen — fließt in die Mindest-Constraints ein.
    filled = {}  # (room_id, role) -> int
    open_idx_by = {}  # (room_id, role) -> list[j]
    for room in state["rooms"]:
        for kind in ("speaker", "judge"):
            filled.setdefault((room["room_id"], kind), 0)
            open_idx_by.setdefault((room["room_id"], kind), [])
        for s in room["slots"]:
            if s["participant"] is None:
                open_idx_by[(room["room_id"], s["role"])].append(len(open_slots))
                open_slots.append((s["slot_id"], room, s["role"]))
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

    # Reine Judges dürfen nicht in Speaker-Slots.
    for i, (_pid, p) in enumerate(unplaced):
        if p[3] == "J":
            for j, (_sid, _room, srole) in enumerate(open_slots):
                if srole == "speaker":
                    model.Add(x[i, j] == 0)

    # Harte Pro-Raum-Constraints: ≥ MIN_SPEAKER Speaker, ≥ MIN_JUDGES Judges.
    # Manuell vorbelegte Slots zählen bereits mit; der Solver muss nur den Rest auffüllen.
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

    cost_terms = []
    for i, (_pid, p) in enumerate(unplaced):
        _name, lang, fmt, role, could_speak, exp = p
        for j, (_sid, room, srole) in enumerate(open_slots):
            if lang in ("DE", "EN") and lang != room["language"]:
                cost_terms.append(W_LANG * x[i, j])
            if fmt in ("BP", "OPD") and fmt != room["format"]:
                cost_terms.append(W_FORMAT * x[i, j])
            if role == "S" and srole == "judge":
                cost_terms.append(W_ROLE * x[i, j])
                if not could_speak:
                    cost_terms.append(W_FORCED_JUDGE * x[i, j])
            if int(exp) == 1 and srole == "judge":
                cost_terms.append(W_EXP_BEGINNER_JUDGE * x[i, j])

        # Strafe, wenn Person ungeplatzt bleibt — Existenz-Anreiz.
        placed_i = model.NewBoolVar(f"pl_{i}")
        model.Add(sum(x[i, j] for j in range(m)) == 1).OnlyEnforceIf(placed_i)
        model.Add(sum(x[i, j] for j in range(m)) == 0).OnlyEnforceIf(placed_i.Not())
        cost_terms.append(W_UNPLACED * placed_i.Not())

    model.Minimize(sum(cost_terms))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {**state, "objective": None, "status": solver.StatusName(status)}

    for i in range(n):
        for j in range(m):
            if solver.Value(x[i, j]):
                conn.execute("UPDATE slots SET participant_id = ? WHERE id = ?",
                             (unplaced[i][0], open_slots[j][0]))
    conn.commit()

    return {**get_rooms(conn, code),
            "objective": solver.ObjectiveValue(),
            "status": solver.StatusName(status)}


# ═══════════════════════════════════════════════════════════════════════════
#  Pretty printing
# ═══════════════════════════════════════════════════════════════════════════
def print_rooms(state: dict) -> None:
    if "status" in state:
        print(f"Status: {state.get('status')} | Strafpunkte: {state.get('objective')}")
    for room in state["rooms"]:
        print(f"\n── Raum {room['index'] + 1} | {room['language']} | "
              f"{room['format']} ──")
        for s in room["slots"]:
            p = s["participant"]
            if p:
                tag = (f"{p['name']:18s}  Wunsch: {p['language']}/{p['format']}/"
                       f"{p['role']} (Exp {p['experience']})")
            else:
                tag = "<leer>"
            print(f"  [{s['role']:7s} {s['subrole']:9s}] {tag}")


# ═══════════════════════════════════════════════════════════════════════════
#  Demo
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import os
    import random
    from generate_participants import generate_participants

    demo_db = os.path.join(os.path.dirname(__file__), "tournaments.db")
    if os.path.exists(demo_db):
        os.remove(demo_db)

    code = "123456789"
    random.seed(0)
    ps = generate_participants()

    conn = init_db(demo_db)
    store_tournament(conn, code, ps)

    print(f"=== Phase 1: propose_rooms({code}) — leere Gerüste ===")
    proposal = propose_rooms(conn, code)
    print_rooms(proposal)

    # Simuliere einen manuellen Eingriff durchs UI:
    # Tabmaster will Teilnehmer_01 unbedingt auf OG-Position in Raum 1 setzen.
    target_slot = proposal["rooms"][0]["slots"][0]
    pid = conn.execute(
        "SELECT id FROM participants WHERE tournament_code = ? AND name = ?",
        (code, "Teilnehmer_01"),
    ).fetchone()[0]
    print(f"\n>>> Manuell: Teilnehmer_01 (id={pid}) → Slot {target_slot['slot_id']} "
          f"({target_slot['subrole']} in Raum {proposal['rooms'][0]['index'] + 1})")
    assign_participant(conn, target_slot["slot_id"], pid)

    print(f"\n=== Phase 2: fill_remaining({code}) — Rest optimal füllen ===")
    final = fill_remaining(conn, code)
    print_rooms(final)
    conn.close()
    print(f"\nDB liegt unter: {demo_db}")
