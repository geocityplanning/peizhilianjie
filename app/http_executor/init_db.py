from __future__ import annotations

import argparse
from pathlib import Path

from .settings import load_settings
from .store import initialize_database


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Initialize the Hermes HTTP executor database")
    parser.add_argument("--db", default=str(settings.db_path), help="SQLite database path")
    args = parser.parse_args()
    initialize_database(Path(args.db))
    print(f"Initialized Hermes executor database: {args.db}")


if __name__ == "__main__":
    main()
