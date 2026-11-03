"""Tests for the workflow file parser."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from durable.config import ConfigError, Retry, load, parse


def document(**overrides):
    base = {
        "version": 1,
        "workflows": {
            "nightly": {
                "jobs": {
                    "fetch": {"command": "fetch.sh"},
                    "build": {"command": "make build", "needs": ["fetch"]},
                }
            }
        },
    }
    base.update(overrides)
    return base


class Valid(unittest.TestCase):
    def test_minimal_document(self) -> None:
        spec = parse(document())
        self.assertEqual(spec.version, 1)
        workflow = spec.workflows["nightly"]
        self.assertEqual(set(workflow.jobs), {"fetch", "build"})
        self.assertEqual(workflow.jobs["build"].needs, ("fetch",))

    def test_defaults_are_conservative(self) -> None:
        spec = parse(document())
        job = spec.workflows["nightly"].jobs["fetch"]
        self.assertEqual(job.retry.max_attempts, 1)
        self.assertIsNone(job.timeout_s)
        self.assertFalse(job.continue_on_error)

    def test_order_is_topological_and_stable(self) -> None:
        spec = parse(
            {
                "version": 1,
                "workflows": {
                    "w": {
                        "jobs": {
                            "c": {"command": "c", "needs": ["b"]},
                            "b": {"command": "b", "needs": ["a"]},
                            "a": {"command": "a"},
                            "z": {"command": "z"},
                        }
                    }
                },
            }
        )
        self.assertEqual(spec.workflows["w"].order(), ["a", "z", "b", "c"])

    def test_retry_settings(self) -> None:
        spec = parse(
            {
                "version": 1,
                "workflows": {
                    "w": {
                        "jobs": {
                            "j": {
                                "command": "x",
                                "retry": {"max": 3, "backoff": "fixed", "base_seconds": 2.5},
                                "timeout_seconds": 30,
                                "continue_on_error": True,
                            }
                        }
                    }
                },
            }
        )
        job = spec.workflows["w"].jobs["j"]
        self.assertEqual(job.retry, Retry(max_attempts=3, backoff="fixed", base_seconds=2.5))
        self.assertEqual(job.timeout_s, 30.0)
        self.assertTrue(job.continue_on_error)

    def test_backoff_delays(self) -> None:
        exponential = Retry(max_attempts=4, backoff="exponential", base_seconds=1.0)
        self.assertEqual(exponential.delay_for(1), 0.0)
        self.assertEqual(exponential.delay_for(2), 1.0)
        self.assertEqual(exponential.delay_for(3), 2.0)
        self.assertEqual(exponential.delay_for(4), 4.0)
        fixed = Retry(max_attempts=3, backoff="fixed", base_seconds=7.0)
        self.assertEqual(fixed.delay_for(3), 7.0)


class Invalid(unittest.TestCase):
    def assert_error(self, payload, path: str, fragment: str = "") -> None:
        with self.assertRaises(ConfigError) as caught:
            parse(payload)
        self.assertEqual(caught.exception.path, path)
        if fragment:
            self.assertIn(fragment, caught.exception.message)

    def test_not_an_object(self) -> None:
        self.assert_error([], "$")

    def test_wrong_version(self) -> None:
        self.assert_error(document(version=2), "version")

    def test_no_workflows(self) -> None:
        self.assert_error({"version": 1, "workflows": {}}, "workflows")

    def test_unknown_top_level_field(self) -> None:
        self.assert_error(document(extra=1), "$", "unknown field")

    def test_missing_command(self) -> None:
        payload = {"version": 1, "workflows": {"w": {"jobs": {"j": {"needs": []}}}}}
        self.assert_error(payload, "workflows.w.jobs.j.command")

    def test_empty_command(self) -> None:
        payload = {"version": 1, "workflows": {"w": {"jobs": {"j": {"command": "  "}}}}}
        self.assert_error(payload, "workflows.w.jobs.j.command")

    def test_unknown_dependency(self) -> None:
        payload = {"version": 1, "workflows": {"w": {"jobs": {"j": {"command": "x", "needs": ["ghost"]}}}}}
        self.assert_error(payload, "workflows.w.jobs.j.needs[0]", "unknown job")

    def test_self_dependency(self) -> None:
        payload = {"version": 1, "workflows": {"w": {"jobs": {"j": {"command": "x", "needs": ["j"]}}}}}
        self.assert_error(payload, "workflows.w.jobs.j.needs[0]", "itself")

    def test_duplicate_needs(self) -> None:
        payload = {
            "version": 1,
            "workflows": {
                "w": {
                    "jobs": {
                        "a": {"command": "a"},
                        "b": {"command": "b", "needs": ["a", "a"]},
                    }
                }
            },
        }
        self.assert_error(payload, "workflows.w.jobs.b.needs", "duplicate")

    def test_cycle(self) -> None:
        payload = {
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
        self.assert_error(payload, "workflows.w.jobs", "cycle")

    def test_retry_max_must_be_positive(self) -> None:
        payload = {"version": 1, "workflows": {"w": {"jobs": {"j": {"command": "x", "retry": {"max": 0}}}}}}
        self.assert_error(payload, "workflows.w.jobs.j.retry.max")

    def test_retry_backoff_must_be_known(self) -> None:
        payload = {"version": 1, "workflows": {"w": {"jobs": {"j": {"command": "x", "retry": {"backoff": "linear"}}}}}}
        self.assert_error(payload, "workflows.w.jobs.j.retry.backoff")

    def test_timeout_must_be_positive(self) -> None:
        payload = {"version": 1, "workflows": {"w": {"jobs": {"j": {"command": "x", "timeout_seconds": 0}}}}}
        self.assert_error(payload, "workflows.w.jobs.j.timeout_seconds")

    def test_job_name_charset(self) -> None:
        payload = {"version": 1, "workflows": {"w": {"jobs": {"bad name": {"command": "x"}}}}}
        self.assert_error(payload, "workflows.w.jobs.bad name")

    def test_continue_on_error_must_be_bool(self) -> None:
        payload = {"version": 1, "workflows": {"w": {"jobs": {"j": {"command": "x", "continue_on_error": "yes"}}}}}
        self.assert_error(payload, "workflows.w.jobs.j.continue_on_error")


class Files(unittest.TestCase):
    def test_load_reads_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "durable.json"
            path.write_text(json.dumps(document()), encoding="utf-8")
            spec = load(Path(tmp))
        self.assertIn("nightly", spec.workflows)

    def test_missing_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError) as caught:
                load(Path(tmp))
        self.assertIn("not found", caught.exception.message)

    def test_broken_json_reports_a_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "durable.json").write_text("{ nope", encoding="utf-8")
            with self.assertRaises(ConfigError) as caught:
                load(Path(tmp))
        self.assertIn("line", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
