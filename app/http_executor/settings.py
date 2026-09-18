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
    fake_mode: bool = False
    auto_login: bool = False


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _fallback_load_env(env_file: Path) -> None:
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def load_project_env(root: Path | None = None) -> Path:
    """Load project-root `.env` without overriding already-set variables."""
    project_root = Path(root) if root is not None else PROJECT_ROOT
    env_file = project_root / ".env"
    if env_file.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(dotenv_path=env_file, override=False)
        except Exception:
            _fallback_load_env(env_file)
    return project_root


def load_settings() -> Settings:
    load_project_env()
    raw_db_path = os.getenv("HERMES_EXECUTOR_DB", "")
    db_path = Path(raw_db_path) if raw_db_path else PROJECT_ROOT / "data" / "http_executor" / "executor.db"
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    return Settings(
        db_path=db_path,
        environment=os.getenv("HERMES_EXECUTOR_ENV", "TEST").upper(),
        auth_token=os.getenv("HERMES_EXECUTOR_TOKEN", "").strip(),
        fake_mode=_env_flag("HERMES_EXECUTOR_FAKE_MODE"),
        auto_login=_env_flag("HERMES_EXECUTOR_AUTO_LOGIN"),
    )
