"""Environment-driven configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var. Unset → `default`. 'false', '0', 'no', 'off'
    (any case) → False; anything else → True."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


@dataclass(frozen=True)
class Settings:
    base_url: str
    db_uri: str
    imprint_text: str
    log_registrations: bool

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
        # In-memory only: nothing is written to disk and all data is wiped on
        # restart (data-protection by design). See app.db for why a single
        # serialised connection is used rather than one per request.
        db_uri = "file:isartab?mode=memory&cache=shared"
        # Free-form Impressum block shown verbatim on /legal. Newlines are
        # preserved; when empty the page renders a placeholder listing the
        # legally required fields.
        imprint_text = os.environ.get("IMPRINT_TEXT", "").strip()
        # When on (default), every accepted registration/modification is
        # printed to stdout so the history can be reconstructed from the
        # container logs even though the DB is in-memory only. Set
        # LOG_REGISTRATIONS=false to suppress it (logs participant names and
        # free-text requests — personal data). See app.routers.events.
        log_registrations = _env_bool("LOG_REGISTRATIONS", True)
        return cls(
            base_url=base_url,
            db_uri=db_uri,
            imprint_text=imprint_text,
            log_registrations=log_registrations,
        )


settings = Settings.from_env()
