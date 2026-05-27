"""Public event + participant API (no admin token required)."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query, status

from .. import db, solver
from ..deps import ConnDep, EventDep
from ..models import (
    EventPublicState,
    ParticipantDTO,
    ParticipantSubmitRequest,
    ParticipantWithSlot,
    SlotDTO,
)

router = APIRouter(prefix="/api/events", tags=["events"])


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


# ─── GET /api/events/{code}/public ─────────────────────────────────────────
@router.get("/{code}/public", response_model=EventPublicState)
def get_public_state(event: EventDep, conn: ConnDep) -> EventPublicState:
    return EventPublicState(
        code=event["code"],
        status=event["status"],
        participant_count=db.count_participants(conn, event["code"]),
        reg_deadline=event["reg_deadline"],
    )


# ─── POST /api/events/{code}/participants ──────────────────────────────────
@router.post(
    "/{code}/participants",
    response_model=ParticipantDTO,
    status_code=status.HTTP_201_CREATED,
)
def submit_participant(
    body: ParticipantSubmitRequest, event: EventDep, conn: ConnDep
) -> ParticipantDTO:
    if event["status"] != "open":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Registration is {event['status']}"
        )

    # Defense in depth: the form caps `name` at 12 chars and
    # `special_request` at 60, both via maxlength — but anyone POSTing
    # directly can bypass that. Truncate silently here too.
    body.name = body.name[:12]
    if body.special_request is not None:
        body.special_request = body.special_request[:60]

    existing = db.get_participant_by_browser_token(
        conn, event["code"], body.browser_token
    )
    try:
        if existing is None:
            # Fresh registration.
            db.insert_participant(
                conn,
                event_code=event["code"],
                browser_token=body.browser_token,
                name=body.name,
                language=body.language,
                format=body.format,
                role=body.role,
                could_speak_last=body.could_speak_last,
                experience=body.experience,
                special_request=body.special_request,
                forced_judge_last=body.forced_judge_last,
            )
        else:
            # Modify: same browser token, update the existing row in place.
            # The participant's slot assignment (if any) stays — the admin
            # can re-evaluate; mismatch tinting on the admin panel will
            # highlight any newly-broken preferences.
            db.update_participant_by_browser_token(
                conn,
                event_code=event["code"],
                browser_token=body.browser_token,
                name=body.name,
                language=body.language,
                format=body.format,
                role=body.role,
                could_speak_last=body.could_speak_last,
                experience=body.experience,
                special_request=body.special_request,
                forced_judge_last=body.forced_judge_last,
            )
    except sqlite3.IntegrityError as exc:
        msg = str(exc).lower()
        if "participants.name" in msg or "event_code, name" in msg:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Name already taken"
            ) from exc
        raise

    row = db.get_participant_by_browser_token(
        conn, event["code"], body.browser_token
    )
    assert row is not None
    return _row_to_participant_dto(row)


# ─── GET /api/events/{code}/participants/me ────────────────────────────────
@router.get(
    "/{code}/participants/me",
    response_model=ParticipantWithSlot,
)
def get_my_participant(
    event: EventDep,
    conn: ConnDep,
    token: Annotated[str, Query(min_length=32, max_length=32,
                                pattern=r"^[a-f0-9]+$")],
) -> ParticipantWithSlot:
    row = db.get_participant_by_browser_token(conn, event["code"], token)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Participant not found")

    # Find the slot (if any) this participant currently sits in.
    slot_dto: Optional[SlotDTO] = None
    pid = int(row["id"])
    rooms_state = solver.get_rooms(conn, event["code"])
    for room in rooms_state["rooms"]:
        for s in room["slots"]:
            if s["participant"] and s["participant"]["id"] == pid:
                p = s["participant"]
                slot_dto = SlotDTO(
                    slot_id=s["slot_id"],
                    role=s["role"],
                    subrole=s["subrole"],
                    slot_index=s["slot_index"],
                    locked=s["locked"],
                    participant=ParticipantDTO(
                        id=p["id"], name=p["name"],
                        language=p["language"], format=p["format"],
                        role=p["role"],
                        could_speak_last=p["could_speak_last"],
                        experience=int(p["experience"]),
                        special_request=None,
                    ),
                )
                break
        if slot_dto is not None:
            break

    return ParticipantWithSlot(
        participant=_row_to_participant_dto(row),
        event_status=event["status"],
        slot=slot_dto,
    )
