import sqlite3
from pathlib import Path

from app.core.config import settings


def get_connection() -> sqlite3.Connection:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.database_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db() -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(schema_path.read_text(encoding="utf-8"))

