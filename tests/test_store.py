"""Tests for the SQLite layer (M0: schema and connection handling)."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from durable import store
from durable.store.schema import EXPECTED_TABLES, SCHEMA_VERSION

NOW = "2026-11-03T00:00:00Z"


class Schema(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / ".durable" / "durable.db"

    def test_creates_every_table(self) -> None:
        conn = store.initialize(self.db_path, NOW)
        self.addCleanup(conn.close)
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        self.assertTrue(EXPECTED_TABLES <= names)

    def test_records_the_schema_version_once(self) -> None:
        conn = store.initialize(self.db_path, NOW)
        self.addCleanup(conn.close)
        self.assertEqual(store.current_version(conn), SCHEMA_VERSION)
        rows = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        self.assertEqual(rows, 1)

    def test_apply_is_idempotent(self) -> None:
        conn = store.initialize(self.db_path, NOW)
        self.addCleanup(conn.close)
        conn.close()
        conn = store.initialize(self.db_path, "2026-11-04T00:00:00Z")
        self.addCleanup(conn.close)
        rows = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        self.assertEqual(rows, 1)

    def test_empty_database_reports_version_zero(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        self.assertEqual(store.current_version(conn), 0)

    def test_foreign_keys_are_enforced(self) -> None:
        conn = store.initialize(self.db_path, NOW)
        self.addCleanup(conn.close)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO job (id, workflow_id, name, command) VALUES (?, ?, ?, ?)",
                ("j1", "missing-workflow", "build", "make"),
            )

    def test_wal_mode_is_on(self) -> None:
        conn = store.initialize(self.db_path, NOW)
        self.addCleanup(conn.close)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_duplicate_job_name_per_workflow_is_rejected(self) -> None:
        conn = store.initialize(self.db_path, NOW)
        self.addCleanup(conn.close)
        conn.execute(
            "INSERT INTO workflow (id, name, spec_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("w1", "nightly", "{}", NOW, NOW),
        )
        conn.execute(
            "INSERT INTO job (id, workflow_id, name, command) VALUES (?, ?, ?, ?)",
            ("j1", "w1", "build", "make"),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO job (id, workflow_id, name, command) VALUES (?, ?, ?, ?)",
                ("j2", "w1", "build", "make again"),
            )

    def test_default_path_lives_under_state_dir(self) -> None:
        path = store.default_db_path(Path("/tmp/project"))
        self.assertEqual(path, Path("/tmp/project/.durable/durable.db"))

    def test_connect_creates_parent_directories(self) -> None:
        nested = Path(self._tmp.name) / "a" / "b" / "state.db"
        conn = store.connect(nested)
        self.addCleanup(conn.close)
        self.assertTrue(nested.exists())


if __name__ == "__main__":
    unittest.main()
