"""HTML routes (landing, register, waiting, rooms, admin).

Chunk 5 scope: register/waiting/rooms templates + /sw.js. The
/register and /waiting shells are intentionally event-agnostic
(JS reads ?event=… at runtime) so the service worker can cache
them once and serve any event.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .. import db, solver
from ..deps import ConnDep
from ..settings import settings

router = APIRouter()

APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> Response:
    return templates.TemplateResponse(request, "landing.html", {})


@router.get("/legal", response_class=HTMLResponse)
def legal_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "legal.html",
        {"imprint_text": settings.imprint_text},
        headers={"Cache-Control": "max-age=3600"},
    )


@router.post("/events")
def create_event(conn: ConnDep) -> RedirectResponse:
    event = db.create_event(conn)
    # 303 to the "event created" success page, which shows the participant
    # link, the admin link, and a way into the admin panel.
    return RedirectResponse(
        url=f"/created/{event['admin_token']}", status_code=303
    )


@router.get("/created/{admin_token}", response_class=HTMLResponse)
def created_page(
    request: Request, admin_token: str, conn: ConnDep
) -> Response:
    event = db.get_event_by_admin_token(conn, admin_token)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return templates.TemplateResponse(
        request,
        "created.html",
        {"event_code": event["code"], "admin_token": admin_token},
    )


# ─── Participant shell pages (cacheable, event-agnostic) ───────────────────
@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request) -> Response:
    """Static shell — JS reads ?event=… at runtime. SW caches this.

    `no-cache` (revalidate, don't serve blind) rather than a max-age: the
    shell changes on every deploy, and a fresh-by-age copy in the browser's
    HTTP cache would otherwise be handed to the service worker's revalidation
    fetch and written back over the cached shell, pinning it to the old build.
    """
    return templates.TemplateResponse(
        request, "register.html", {},
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/waiting", response_class=HTMLResponse)
def waiting_page(request: Request) -> Response:
    """Static shell — JS reads ?event=… and ?me=… from localStorage."""
    return templates.TemplateResponse(
        request, "waiting.html", {},
    )


# ─── Public room view (SSR — needs DB state) ───────────────────────────────
@router.get("/rooms", response_class=HTMLResponse)
def rooms_page(
    request: Request, conn: ConnDep,
    event: str, me: Optional[str] = None,
) -> Response:
    event_row = db.get_event_by_code(conn, event)
    if event_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")

    my_participant_id: Optional[int] = None
    if me:
        p = db.get_participant_by_browser_token(conn, event, me)
        if p is not None:
            my_participant_id = int(p["id"])

    rooms_state = solver.get_rooms(conn, event)

    # If we have the user's participant id and they're actually placed in a
    # slot, compute a short label like "OPD Judge" / "BP Speaker" for the
    # header. Otherwise the header falls back to plain "Rooms".
    my_role_label: Optional[str] = None
    my_room_label: Optional[str] = None
    if my_participant_id is not None:
        for room in rooms_state["rooms"]:
            for slot in room["slots"]:
                p = slot["participant"]
                if p and p["id"] == my_participant_id:
                    role_word = "Speaker" if slot["role"] == "speaker" else "Judge"
                    my_role_label = f"{room['format']} {role_word}"
                    nm = (room.get("name") or "").strip()
                    my_room_label = nm if nm else f"Room {room['index'] + 1}"
                    break
            if my_role_label is not None:
                break

    return templates.TemplateResponse(
        request,
        "rooms.html",
        {
            "event_code": event,
            "event_status": event_row["status"],
            "rooms": rooms_state["rooms"],
            "my_participant_id": my_participant_id,
            "my_role_label": my_role_label,
            "my_room_label": my_room_label,
        },
    )


# ─── Admin panel ───────────────────────────────────────────────────────────
@router.get("/admin/{admin_token}", response_class=HTMLResponse)
def admin_panel(
    request: Request, admin_token: str, conn: ConnDep
) -> Response:
    event = db.get_event_by_admin_token(conn, admin_token)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return templates.TemplateResponse(
        request, "admin.html", {"admin_token": admin_token},
    )


# ─── Service worker (must be served from root scope) ───────────────────────
@router.get("/sw.js", include_in_schema=False)
def serve_sw() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "js" / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )
