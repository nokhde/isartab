"""Public event + participant API (no admin token required)."""
from __future__ import annotations

import logging
import sqlite3
import sys
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
from ..settings import settings

router = APIRouter(prefix="/api/events", tags=["events"])

# ─── Forensic registration log ─────────────────────────────────────────────
# NOTE: this writes participant names and free-text special requests to the
# logs — personal data that the in-memory DB deliberately never persists. Keep
# log retention short and access-controlled.
_reg_log = logging.getLogger("isartab.registration")
if not _reg_log.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [registration] %(message)s")
    )
    _reg_log.addHandler(_handler)
    _reg_log.setLevel(logging.INFO)
    _reg_log.propagate = False


def _log_registration(event_code: str, action: str, row: sqlite3.Row) -> None:
    """Print one registration event to stdout for log-based recovery.
    `action` is 'register', 'modify' or 'unregister'. Logged from the stored
    row so it reflects exactly what was persisted (e.g. after name truncation);
    for 'unregister' the row is the state just before deletion.

    No-op when LOG_REGISTRATIONS is disabled (settings.log_registrations)."""
    if not settings.log_registrations:
        return
    _reg_log.info(
        "event=%s action=%s pid=%s name=%r language=%s format=%s role=%s "
        "could_speak_last=%s experience=%s forced_judge_last=%s "
        "special_request=%r",
        event_code,
        action,
        row["id"],
        row["name"],
        row["language"],
        row["format"],
        row["role"],
        bool(row["could_speak_last"]),
        int(row["experience"]),
        bool(row["forced_judge_last"]),
        row["special_request"],
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
    # directly can bypass that. Truncate here too, marking cut-off names
    # with a trailing "..." so the truncation is visible.
    if len(body.name) > 12:
        body.name = body.name[:12] + "..."
    if body.special_request is not None:
        body.special_request = body.special_request[:60]

    existing = db.get_participant_by_browser_token(
        conn, event["code"], body.browser_token
    )
    action = "register" if existing is None else "modify"
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
    _log_registration(event["code"], action, row)
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


# ─── DELETE /api/events/{code}/participants/me ─────────────────────────────
@router.delete("/{code}/participants/me")
def delete_my_participant(
    event: EventDep,
    conn: ConnDep,
    token: Annotated[str, Query(min_length=32, max_length=32,
                                pattern=r"^[a-f0-9]+$")],
) -> dict[str, str]:
    """Self-service unregister: a participant pulls their own entry using the
    browser token from localStorage. Only while registration is open — once
    the event is closed/published the allocation is the admin's to manage
    (mirrors submit_participant, which also requires status 'open')."""
    if event["status"] != "open":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Registration is {event['status']}"
        )
    row = db.get_participant_by_browser_token(conn, event["code"], token)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Participant not found")
    db.delete_participant_by_browser_token(conn, event["code"], token)
    _log_registration(event["code"], "unregister", row)
    return {"status": "unregistered"}
