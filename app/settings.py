"""Environment-driven configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    base_url: str
    db_uri: str
    imprint_text: str

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
        return cls(base_url=base_url, db_uri=db_uri, imprint_text=imprint_text)


settings = Settings.from_env()
