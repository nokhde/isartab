"""Environment-driven configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    base_url: str
    db_uri: str

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
        # In-memory only: nothing is written to disk and all data is wiped on
        # restart (data-protection by design). The shared cache lets every
        # per-request connection see the same database; app.db holds one
        # keep-alive connection open so the DB survives between requests.
        db_uri = "file:isartab?mode=memory&cache=shared"
        return cls(base_url=base_url, db_uri=db_uri)


settings = Settings.from_env()
