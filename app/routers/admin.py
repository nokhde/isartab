"""Admin API (token-gated).

Chunk 4 scope: /state, /close-registration, /reopen-registration,
PATCH /event. The room/slot endpoints land in Chunk 6.
"""
from __future__ import annotations

import random
import sqlite3

from fastapi import APIRouter, HTTPException, status

from .. import db, solver
from ..deps import AdminEventDep, ConnDep
from ..models import (
    AdminStateResponse,
    EventDTO,
    EventUpdateRequest,
    ParticipantDTO,
    RoomCreateRequest,
    RoomDTO,
    RoomUpdateRequest,
    SlotDTO,
    SlotPatchRequest,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _row_to_event_dto(row: sqlite3.Row) -> EventDTO:
    return EventDTO(
        code=row["code"],
        status=row["status"],
        reg_deadline=row["reg_deadline"],
        room_limit=row["room_limit"],
        created_at=row["created_at"],
    )


def _row_to_participant_dto(row: sqlite3.Row) -> ParticipantDTO:
    return ParticipantDTO(
        id=row["id"],
        name=row["name"],
        language=row["language"],
        format=row["format"],
        role=row["role"],
        could_speak_last=bool(row["could_speak_last"]),
        experience=int(row["experience"]),
        special_request=row["special_request"],
        forced_judge_last=bool(row["forced_judge_last"]),
    )


def _build_state(event: sqlite3.Row, conn: sqlite3.Connection) -> AdminStateResponse:
    participants = [
        _row_to_participant_dto(r) for r in db.list_participants(conn, event["code"])
    ]
    # solver.get_rooms returns the full room/slot structure with `locked` and
    # nested participants. Until Chunk 6, this is empty for new events.
    rooms_state = solver.get_rooms(conn, event["code"])
    rooms = [
        RoomDTO(
            room_id=room["room_id"],
            index=room["index"],
            name=room.get("name"),
            format=room["format"],
            language=room["language"],
            slots=[
                SlotDTO(
                    slot_id=s["slot_id"],
                    role=s["role"],
                    subrole=s["subrole"],
                    slot_index=s["slot_index"],
                    locked=s["locked"],
                    participant=ParticipantDTO(
                        id=s["participant"]["id"],
                        name=s["participant"]["name"],
                        language=s["participant"]["language"],
                        format=s["participant"]["format"],
                        role=s["participant"]["role"],
                        could_speak_last=s["participant"]["could_speak_last"],
                        experience=int(s["participant"]["experience"]),
                        special_request=s["participant"].get("special_request"),
                        forced_judge_last=bool(
                            s["participant"].get("forced_judge_last", False)
                        ),
                    ) if s["participant"] else None,
                )
                for s in room["slots"]
            ],
        )
        for room in rooms_state["rooms"]
    ]
    return AdminStateResponse(
        event=_row_to_event_dto(event),
        participants=participants,
        rooms=rooms,
    )


# ─── GET /api/admin/{admin_token}/state ────────────────────────────────────
@router.get("/{admin_token}/state", response_model=AdminStateResponse)
def get_admin_state(event: AdminEventDep, conn: ConnDep) -> AdminStateResponse:
    return _build_state(event, conn)


# ─── POST /api/admin/{admin_token}/close-registration ──────────────────────
@router.post(
    "/{admin_token}/close-registration", response_model=AdminStateResponse
)
def close_registration(event: AdminEventDep, conn: ConnDep) -> AdminStateResponse:
    if event["status"] != "open":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot close — event is {event['status']}",
        )
    db.update_event_status(conn, event["code"], "closed")
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


# ─── POST /api/admin/{admin_token}/reopen-registration ─────────────────────
@router.post(
    "/{admin_token}/reopen-registration", response_model=AdminStateResponse
)
def reopen_registration(event: AdminEventDep, conn: ConnDep) -> AdminStateResponse:
    if event["status"] != "closed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot reopen — event is {event['status']}",
        )
    db.update_event_status(conn, event["code"], "open")
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


# ─── PATCH /api/admin/{admin_token}/event ──────────────────────────────────
@router.patch("/{admin_token}/event", response_model=AdminStateResponse)
def patch_event(
    body: EventUpdateRequest, event: AdminEventDep, conn: ConnDep
) -> AdminStateResponse:
    if event["status"] == "published":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Event is published — cannot edit deadline or room limit",
        )
    db.update_event(
        conn, event["code"],
        reg_deadline=body.reg_deadline,
        room_limit=body.room_limit,
    )
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


# ─── Room/slot operations — require status='closed' ────────────────────────
def _require_closed(event: sqlite3.Row) -> None:
    if event["status"] != "closed":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Action requires status='closed'; current is '{event['status']}'",
        )


@router.post("/{admin_token}/propose-rooms", response_model=AdminStateResponse)
def propose_rooms_endpoint(
    event: AdminEventDep, conn: ConnDep
) -> AdminStateResponse:
    _require_closed(event)
    if db.count_participants(conn, event["code"]) == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No participants to allocate"
        )
    solver.propose_rooms(conn, event["code"])
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


@router.post(
    "/{admin_token}/rooms",
    response_model=AdminStateResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_room_endpoint(
    body: RoomCreateRequest, event: AdminEventDep, conn: ConnDep
) -> AdminStateResponse:
    _require_closed(event)
    solver.add_room(conn, event["code"], format=body.format, language=body.language)
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


@router.patch(
    "/{admin_token}/rooms/{room_id}", response_model=AdminStateResponse
)
def patch_room_endpoint(
    room_id: int,
    body: RoomUpdateRequest,
    event: AdminEventDep,
    conn: ConnDep,
) -> AdminStateResponse:
    _require_closed(event)
    row = conn.execute(
        "SELECT 1 FROM rooms WHERE id = ? AND event_code = ?",
        (room_id, event["code"]),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    fields = body.model_fields_set
    if "name" in fields:
        # Empty / whitespace-only strings become NULL so the admin can
        # clear a name they don't want anymore.
        name = (body.name or "").strip() or None
        conn.execute("UPDATE rooms SET name = ? WHERE id = ?", (name, room_id))
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


@router.post(
    "/{admin_token}/rooms/{room_id}/judge-slots",
    response_model=AdminStateResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_judge_slot_endpoint(
    room_id: int, event: AdminEventDep, conn: ConnDep
) -> AdminStateResponse:
    _require_closed(event)
    row = conn.execute(
        "SELECT 1 FROM rooms WHERE id = ? AND event_code = ?",
        (room_id, event["code"]),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    solver.add_judge_slot(conn, room_id)
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


@router.post(
    "/{admin_token}/rooms/{room_id}/free-slots",
    response_model=AdminStateResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_free_slot_endpoint(
    room_id: int, event: AdminEventDep, conn: ConnDep
) -> AdminStateResponse:
    _require_closed(event)
    row = conn.execute(
        "SELECT format FROM rooms WHERE id = ? AND event_code = ?",
        (room_id, event["code"]),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    if row["format"] != "OPD":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Free-speaker slots only exist in OPD rooms",
        )
    solver.add_free_slot(conn, room_id)
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


@router.delete(
    "/{admin_token}/rooms/{room_id}", response_model=AdminStateResponse
)
def delete_room_endpoint(
    room_id: int, event: AdminEventDep, conn: ConnDep
) -> AdminStateResponse:
    _require_closed(event)
    # Confirm room exists and belongs to this event before deleting.
    row = conn.execute(
        "SELECT 1 FROM rooms WHERE id = ? AND event_code = ?",
        (room_id, event["code"]),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Room not found")
    solver.delete_room(conn, event["code"], room_id)
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


@router.post(
    "/{admin_token}/slots/{slot_a}/swap/{slot_b}",
    response_model=AdminStateResponse,
)
def swap_slots_endpoint(
    slot_a: int, slot_b: int, event: AdminEventDep, conn: ConnDep
) -> AdminStateResponse:
    _require_closed(event)
    # Both slots must belong to a room in this event.
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM slots s JOIN rooms r ON r.id = s.room_id "
        "WHERE s.id IN (?, ?) AND r.event_code = ?",
        (slot_a, slot_b, event["code"]),
    ).fetchone()["n"]
    if n != 2:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Slot not found")
    solver.swap_slots(conn, slot_a, slot_b)
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


@router.patch(
    "/{admin_token}/slots/{slot_id}", response_model=AdminStateResponse
)
def patch_slot_endpoint(
    slot_id: int,
    body: SlotPatchRequest,
    event: AdminEventDep,
    conn: ConnDep,
) -> AdminStateResponse:
    _require_closed(event)
    # Slot must belong to a room in this event.
    row = conn.execute(
        "SELECT 1 FROM slots s JOIN rooms r ON r.id = s.room_id "
        "WHERE s.id = ? AND r.event_code = ?",
        (slot_id, event["code"]),
    ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Slot not found")

    fields = body.model_fields_set
    if "participant_id" in fields:
        if body.participant_id is None:
            solver.clear_slot(conn, slot_id)
        else:
            p = conn.execute(
                "SELECT 1 FROM participants WHERE id = ? AND event_code = ?",
                (body.participant_id, event["code"]),
            ).fetchone()
            if p is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, "Participant not in this event"
                )
            solver.assign_participant(conn, slot_id, body.participant_id)
    if "locked" in fields:
        assert body.locked is not None
        solver.set_slot_locked(conn, slot_id, body.locked)

    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


@router.post(
    "/{admin_token}/clear-unlocked", response_model=AdminStateResponse
)
def clear_unlocked_endpoint(
    event: AdminEventDep, conn: ConnDep
) -> AdminStateResponse:
    """Vacates every slot whose `locked = 0` for this event. Same first
    half as magic-fill, exposed standalone so the admin can reset the
    layout without immediately re-running the solver."""
    _require_closed(event)
    conn.execute(
        """
        UPDATE slots SET participant_id = NULL
        WHERE locked = 0
          AND participant_id IS NOT NULL
          AND room_id IN (SELECT id FROM rooms WHERE event_code = ?)
        """,
        (event["code"],),
    )
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


@router.delete(
    "/{admin_token}/participants/{participant_id}",
    response_model=AdminStateResponse,
)
def delete_participant_endpoint(
    participant_id: int, event: AdminEventDep, conn: ConnDep
) -> AdminStateResponse:
    """Permanently remove a participant. Any slot they were in becomes
    empty (ON DELETE SET NULL on slots.participant_id). Allowed in any
    event status — the admin gets to fix a mis-registration even after
    publishing."""
    row = conn.execute(
        "SELECT 1 FROM participants WHERE id = ? AND event_code = ?",
        (participant_id, event["code"]),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Participant not in this event"
        )
    conn.execute(
        "DELETE FROM participants WHERE id = ? AND event_code = ?",
        (participant_id, event["code"]),
    )
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


@router.post("/{admin_token}/magic-fill", response_model=AdminStateResponse)
def magic_fill_endpoint(
    event: AdminEventDep, conn: ConnDep
) -> AdminStateResponse:
    _require_closed(event)
    # Need at least one room.
    row = conn.execute(
        "SELECT 1 FROM rooms WHERE event_code = ? LIMIT 1",
        (event["code"],),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No rooms exist — call /propose-rooms or /rooms first",
        )
    solver.fill_remaining(conn, event["code"])
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


# ─── Demo seeder (registration must still be open) ─────────────────────────
DEMO_COUNT = 30
DEMO_SPECIAL_REQUESTS = ["Vegan dinner", "Bring water"]


@router.post("/{admin_token}/seed-demo", response_model=AdminStateResponse)
def seed_demo_endpoint(
    event: AdminEventDep, conn: ConnDep
) -> AdminStateResponse:
    """Insert up to DEMO_COUNT Demo_NN participants with realistic random
    attributes. Idempotent: names that already exist are silently skipped,
    so clicking the button twice doesn't double-seed."""
    if event["status"] != "open":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Seeding only allowed while open; current is '{event['status']}'",
        )

    # Two random demo participants get a short special_request so the UI
    # rendering of the request field is exercised by default.
    special_picks = random.sample(range(DEMO_COUNT), len(DEMO_SPECIAL_REQUESTS))
    special_map = dict(zip(special_picks, DEMO_SPECIAL_REQUESTS))

    for i in range(1, DEMO_COUNT + 1):
        name = f"Demo_{i:02d}"
        language = random.choices(
            ["DE", "EN", "DE/EN"], weights=[25, 50, 25]
        )[0]
        fmt = random.choices(
            ["BP", "OPD", "egal"], weights=[75, 15, 10]
        )[0]
        # OPD in the German-speaking scene skews bilingual / German-speaker.
        if fmt == "OPD":
            language = random.choices(["EN", "DE/EN"], weights=[75, 25])[0]
        role = random.choices(["S", "J", "SJ"], weights=[65, 20, 15])[0]
        could_speak_last = random.choices(
            [True, False], weights=[95, 5]
        )[0]
        experience = random.choices([1, 2, 3], weights=[35, 40, 25])[0]

        try:
            db.insert_participant(
                conn,
                event_code=event["code"],
                browser_token=db.gen_browser_token(),
                name=name,
                language=language,
                format=fmt,
                role=role,
                could_speak_last=could_speak_last,
                experience=experience,
                special_request=special_map.get(i - 1),
            )
        except sqlite3.IntegrityError:
            # Name already taken — assume a previous seed; just skip.
            continue

    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)


@router.post("/{admin_token}/publish", response_model=AdminStateResponse)
def publish_endpoint(
    event: AdminEventDep, conn: ConnDep
) -> AdminStateResponse:
    _require_closed(event)
    row = conn.execute(
        "SELECT 1 FROM rooms WHERE event_code = ? LIMIT 1",
        (event["code"],),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Cannot publish — no rooms exist"
        )
    db.update_event_status(conn, event["code"], "published")
    refreshed = db.get_event_by_code(conn, event["code"])
    assert refreshed is not None
    return _build_state(refreshed, conn)
