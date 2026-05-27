"""Environment-driven configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    base_url: str
    db_path: Path

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("DATA_DIR", "./data")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
        db_path = data_dir / "tournaments.db"
        return cls(data_dir=data_dir, base_url=base_url, db_path=db_path)


settings = Settings.from_env()
