"""Connection handling: pragmas, directory creation, one place to open the db."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import schema

DEFAULT_STATE_DIR = ".durable"
DEFAULT_DB_NAME = "durable.db"

#: Milliseconds SQLite waits for a writer before returning SQLITE_BUSY.
BUSY_TIMEOUT_MS = 5_000


def default_db_path(root: Path) -> Path:
    return Path(root) / DEFAULT_STATE_DIR / DEFAULT_DB_NAME


def connect(path: Path) -> sqlite3.Connection:
    """Open ``path`` with the pragmas the rest of the code assumes."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def open_project(root: Path) -> sqlite3.Connection:
    """Open (creating if needed) the database that belongs to ``root``."""

    return connect(default_db_path(root))


def initialize(path: Path, now: str) -> sqlite3.Connection:
    """Open the database and make sure the schema is present."""

    conn = connect(path)
    schema.apply(conn, now)
    return conn
