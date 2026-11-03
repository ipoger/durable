"""SQLite schema for durable.

The DDL here is the physical side of the model in ``docs/design.md`` §6.
``SCHEMA_VERSION`` is bumped whenever a statement changes; migrations are the
job of M4, which is why every table is created idempotently and the version row
is written once.
"""

from __future__ import annotations

import sqlite3
from typing import List

SCHEMA_VERSION = 1

DDL: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version    INTEGER NOT NULL,
        applied_at TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow (
        id         TEXT PRIMARY KEY,
        name       TEXT NOT NULL UNIQUE,
        spec_json  TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS job (
        id                TEXT PRIMARY KEY,
        workflow_id       TEXT NOT NULL REFERENCES workflow(id) ON DELETE CASCADE,
        name              TEXT NOT NULL,
        command           TEXT NOT NULL,
        depends_on        TEXT NOT NULL DEFAULT '[]',
        retry_max         INTEGER NOT NULL DEFAULT 0,
        backoff_base_s    REAL    NOT NULL DEFAULT 1.0,
        timeout_s         REAL,
        continue_on_error INTEGER NOT NULL DEFAULT 0,
        UNIQUE (workflow_id, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_run (
        id          TEXT PRIMARY KEY,
        workflow_id TEXT NOT NULL REFERENCES workflow(id),
        state       TEXT NOT NULL,
        trigger     TEXT NOT NULL,
        created_at  TEXT NOT NULL,
        finished_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS job_run (
        id              TEXT PRIMARY KEY,
        workflow_run_id TEXT NOT NULL REFERENCES workflow_run(id) ON DELETE CASCADE,
        job_id          TEXT NOT NULL REFERENCES job(id),
        attempt         INTEGER NOT NULL,
        state           TEXT NOT NULL,
        exit_code       INTEGER,
        started_at      TEXT,
        finished_at     TEXT,
        heartbeat_at    TEXT,
        log_path        TEXT,
        error           TEXT,
        UNIQUE (workflow_run_id, job_id, attempt)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        at              TEXT NOT NULL,
        workflow_run_id TEXT,
        job_run_id      TEXT,
        type            TEXT NOT NULL,
        payload_json    TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_job_workflow ON job(workflow_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_run_workflow_run ON job_run(workflow_run_id)",
    "CREATE INDEX IF NOT EXISTS idx_job_run_state ON job_run(state)",
    "CREATE INDEX IF NOT EXISTS idx_event_workflow_run ON event(workflow_run_id)",
]

EXPECTED_TABLES = frozenset(
    {"schema_version", "workflow", "job", "workflow_run", "job_run", "event"}
)


def apply(conn: sqlite3.Connection, now: str) -> None:
    """Create every table if missing and record the schema version once."""

    conn.executescript(";\n".join(DDL) + ";")
    conn.commit()
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, now),
        )
        conn.commit()


def current_version(conn: sqlite3.Connection) -> int:
    """Return the schema version recorded in the file, or 0 for an empty db."""

    try:
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0
