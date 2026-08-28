from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: Path
    environment: str
    mode: str
    auth_token: str
    allow_anonymous: bool
    fake_delay_ms: int
    fake_channel_suffix: str


def load_settings() -> Settings:
    raw_db_path = os.getenv("HERMES_EXECUTOR_DB", "")
    db_path = Path(raw_db_path) if raw_db_path else PROJECT_ROOT / "data" / "http_executor" / "executor.db"
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    raw_delay = os.getenv("HERMES_FAKE_DELAY_MS", "0")
    try:
        fake_delay_ms = max(0, int(raw_delay))
    except ValueError:
        fake_delay_ms = 0

    return Settings(
        db_path=db_path,
        environment=os.getenv("HERMES_EXECUTOR_ENV", "TEST").upper(),
        mode=os.getenv("HERMES_EXECUTOR_MODE", "FAKE").upper(),
        auth_token=os.getenv("HERMES_EXECUTOR_TOKEN", ""),
        allow_anonymous=_as_bool(os.getenv("HERMES_EXECUTOR_ALLOW_ANONYMOUS"), False),
        fake_delay_ms=fake_delay_ms,
        fake_channel_suffix=os.getenv("HERMES_FAKE_CHANNEL_SUFFIX", "-1"),
    )

