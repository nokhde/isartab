"""Pydantic DTOs and shared literal types.

Mirrors the shapes in proposal §3.2. ParticipantDTO never includes the
browser_token — that's a per-browser secret and must not leak across
clients.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ─── Literal types ─────────────────────────────────────────────────────────
Language = Literal["DE", "EN", "DE/EN"]
Format = Literal["BP", "OPD", "egal"]
RoleChoice = Literal["S", "J", "SJ"]
RoomFormat = Literal["BP", "OPD"]
RoomLanguage = Literal["DE", "EN"]
SlotRole = Literal["speaker", "judge"]
EventStatus = Literal["open", "closed", "published"]
Experience = Literal[1, 2, 3]


# ─── Participant ───────────────────────────────────────────────────────────
class ParticipantSubmitRequest(BaseModel):
    """Body of POST /api/events/{code}/participants."""
    model_config = ConfigDict(str_strip_whitespace=True)

    browser_token: str = Field(min_length=32, max_length=32, pattern=r"^[a-f0-9]+$")
    name: str = Field(min_length=1, max_length=80)
    language: Language
    format: Format
    role: RoleChoice
    could_speak_last: bool = True
    experience: Experience
    special_request: Optional[str] = Field(default=None, max_length=500)
    forced_judge_last: bool = False


class ParticipantDTO(BaseModel):
    id: int
    name: str
    language: Language
    format: Format
    role: RoleChoice
    could_speak_last: bool
    experience: int
    special_request: Optional[str] = None
    forced_judge_last: bool = False


class ParticipantWithSlot(BaseModel):
    """Returned by GET /api/events/{code}/participants/me."""
    participant: ParticipantDTO
    event_status: EventStatus
    # Slot lookup wires up in Chunk 6 once rooms exist; until then this is None.
    slot: Optional["SlotDTO"] = None


# ─── Rooms / slots ─────────────────────────────────────────────────────────
class SlotDTO(BaseModel):
    slot_id: int
    role: SlotRole
    subrole: str
    slot_index: int
    locked: bool
    participant: Optional[ParticipantDTO] = None


class RoomDTO(BaseModel):
    room_id: int
    index: int
    name: Optional[str] = None
    format: RoomFormat
    language: RoomLanguage
    slots: list[SlotDTO]


# ─── Events ────────────────────────────────────────────────────────────────
class EventDTO(BaseModel):
    """Public-safe event metadata — no admin_token."""
    code: str
    status: EventStatus
    reg_deadline: Optional[int] = None
    room_limit: int
    created_at: int


class EventPublicState(BaseModel):
    """Returned by GET /api/events/{code}/public — what the waiting page polls."""
    code: str
    status: EventStatus
    participant_count: int
    reg_deadline: Optional[int] = None


class EventUpdateRequest(BaseModel):
    """Body of PATCH /api/admin/{admin_token}/event."""
    reg_deadline: Optional[int] = None
    room_limit: Optional[int] = Field(default=None, ge=1, le=10)


class RoomCreateRequest(BaseModel):
    """Body of POST /api/admin/{admin_token}/rooms."""
    format: RoomFormat
    language: RoomLanguage


class RoomUpdateRequest(BaseModel):
    """Body of PATCH /api/admin/{admin_token}/rooms/{room_id}.

    Currently only the human-readable name is editable. Empty string is
    treated as 'no name' (null) so the admin can clear it.
    """
    name: Optional[str] = Field(default=None, max_length=80)


class SlotPatchRequest(BaseModel):
    """Body of PATCH /api/admin/{admin_token}/slots/{slot_id}.

    Field semantics rely on pydantic's `model_fields_set`:
      - participant_id absent  → don't touch assignment
      - participant_id == null → clear the slot
      - participant_id == int  → assign that participant (vacates their
                                 previous slot if any)
      - locked absent          → don't touch the lock
      - locked == bool         → set the lock flag
    """
    participant_id: Optional[int] = None
    locked: Optional[bool] = None


class AdminStateResponse(BaseModel):
    """Full admin view of an event."""
    event: EventDTO
    participants: list[ParticipantDTO]
    rooms: list[RoomDTO]


class LogRecoverRequest(BaseModel):
    """Body of POST /api/admin/{admin_token}/recover-from-log.

    `log` is a raw paste of container stdout; the server extracts the
    registration lines. `dry_run` powers the modal's live preview — parse and
    report what *would* be recovered without touching the DB."""
    log: str = Field(max_length=5_000_000)
    dry_run: bool = False


class LogRecoverResult(BaseModel):
    """Outcome of a log-recovery run (real or dry)."""
    dry_run: bool
    # Distinct participants parsed from the chosen event's lines.
    detected: int
    # Actually inserted (0 for a dry run).
    recovered: int
    # Parsed but not inserted — duplicate/invalid names rejected by the DB.
    skipped: int
    # Names of the chosen event's survivors, in order (for the preview line).
    names: list[str]
    # The original event code chosen for import (None if nothing parsed).
    event_code: Optional[str] = None
    # Other original event codes present in the paste but not imported.
    other_event_codes: list[str] = Field(default_factory=list)


ParticipantWithSlot.model_rebuild()
