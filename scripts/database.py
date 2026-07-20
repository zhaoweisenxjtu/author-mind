"""Shared database connection (author-knowledge standalone copy).

Connects to ~/.astromind-praxis/astromind_praxis.db
"""

import sqlite3
from pathlib import Path

DB_DIR = Path.home() / ".astromind-praxis"
DB_PATH = DB_DIR / "astromind_praxis.db"


def get_db_path() -> str:
    return str(DB_PATH)


def ensure_db_dir():
    DB_DIR.mkdir(parents=True, exist_ok=True)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def get_connection() -> sqlite3.Connection:
    ensure_db_dir()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn
