from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    db_path: Path
    environment: str
    auth_token: str


def load_settings() -> Settings:
    raw_db_path = os.getenv("HERMES_EXECUTOR_DB", "")
    db_path = Path(raw_db_path) if raw_db_path else PROJECT_ROOT / "data" / "http_executor" / "executor.db"
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    return Settings(
        db_path=db_path,
        environment=os.getenv("HERMES_EXECUTOR_ENV", "TEST").upper(),
        auth_token=os.getenv("HERMES_EXECUTOR_TOKEN", "").strip(),
    )
