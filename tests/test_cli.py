"""End-to-end tests for the CLI surface."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from durable.cli import main


def run(*argv: str) -> tuple:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class InitAndStatus(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_init_creates_config_and_database(self) -> None:
        code, out, _ = run("--root", str(self.root), "init")
        self.assertEqual(code, 0)
        self.assertIn("create", out)
        self.assertTrue((self.root / "durable.json").is_file())
        self.assertTrue((self.root / ".durable" / "durable.db").is_file())

    def test_init_is_idempotent(self) -> None:
        run("--root", str(self.root), "init")
        config_before = (self.root / "durable.json").read_text(encoding="utf-8")
        code, out, _ = run("--root", str(self.root), "init")
        self.assertEqual(code, 0)
        self.assertIn("keep", out)
        self.assertEqual((self.root / "durable.json").read_text(encoding="utf-8"), config_before)

    def test_generated_config_validates(self) -> None:
        run("--root", str(self.root), "init")
        code, out, _ = run("--root", str(self.root), "validate")
        self.assertEqual(code, 0)
        self.assertIn("workflow example", out)

    def test_status_without_init_fails(self) -> None:
        code, _, err = run("--root", str(self.root), "status")
        self.assertEqual(code, 1)
        self.assertIn("durable init", err)

    def test_status_after_init(self) -> None:
        run("--root", str(self.root), "init")
        code, out, _ = run("--root", str(self.root), "status")
        self.assertEqual(code, 0)
        self.assertIn("schema    v1", out)
        self.assertIn("job_run", out)

    def test_status_json(self) -> None:
        run("--root", str(self.root), "init")
        code, out, _ = run("--root", str(self.root), "status", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["counts"]["workflow"], 0)
        self.assertTrue(payload["database"].endswith("durable.db"))


class Validate(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, payload) -> None:
        (self.root / "durable.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_broken_config_returns_one(self) -> None:
        self.write({"version": 1, "workflows": {}})
        code, _, err = run("--root", str(self.root), "validate")
        self.assertEqual(code, 1)
        self.assertIn("invalid", err)
        self.assertIn("workflows", err)

    def test_cycle_is_reported_with_path(self) -> None:
        self.write(
            {
                "version": 1,
                "workflows": {
                    "w": {
                        "jobs": {
                            "a": {"command": "a", "needs": ["b"]},
                            "b": {"command": "b", "needs": ["a"]},
                        }
                    }
                },
            }
        )
        code, _, err = run("--root", str(self.root), "validate")
        self.assertEqual(code, 1)
        self.assertIn("cycle", err)

    def test_prints_jobs_in_order(self) -> None:
        self.write(
            {
                "version": 1,
                "workflows": {
                    "w": {
                        "jobs": {
                            "deploy": {"command": "deploy", "needs": ["test"]},
                            "test": {"command": "test", "needs": ["build"]},
                            "build": {"command": "build"},
                        }
                    }
                },
            }
        )
        code, out, _ = run("--root", str(self.root), "validate")
        self.assertEqual(code, 0)
        positions = [out.index(name) for name in ("build", "test", "deploy")]
        self.assertEqual(positions, sorted(positions))

    def test_missing_config_returns_one(self) -> None:
        code, _, err = run("--root", str(self.root), "validate")
        self.assertEqual(code, 1)
        self.assertIn("not found", err)


if __name__ == "__main__":
    unittest.main()
