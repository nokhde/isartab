"""Shared FastAPI dependencies."""
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import Depends, HTTPException, status

from . import db
from .db import get_conn

ConnDep = Annotated[sqlite3.Connection, Depends(get_conn)]


def require_event(code: str, conn: ConnDep) -> sqlite3.Row:
    """Resolves the event by 9-digit code, 404s otherwise."""
    event = db.get_event_by_code(conn, code)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return event


def require_admin(admin_token: str, conn: ConnDep) -> sqlite3.Row:
    """Resolves the event by admin_token, 404s otherwise. The token is
    treated as the only secret guarding the admin endpoints."""
    event = db.get_event_by_admin_token(conn, admin_token)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return event


EventDep = Annotated[sqlite3.Row, Depends(require_event)]
AdminEventDep = Annotated[sqlite3.Row, Depends(require_admin)]
