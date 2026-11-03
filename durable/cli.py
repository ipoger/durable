"""Command line entry point.

M0 exposes the three commands that make the skeleton verifiable:

* ``init``     — create ``durable.json`` and the state directory
* ``validate`` — parse and check a workflow file without touching the database
* ``status``   — report what is in the state file

``run``, ``logs``, ``retry`` and ``cancel`` arrive with the executor in M2/M5.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from . import __version__, config, store

TEMPLATE = {
    "version": 1,
    "workflows": {
        "example": {
            "jobs": {
                "hello": {"command": "echo hello"},
                "goodbye": {"command": "echo goodbye", "needs": ["hello"]},
            }
        }
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="durable",
        description="A small, durable task orchestrator: SQLite state, shell commands, retries.",
    )
    parser.add_argument(
        "--root",
        default=".",
        metavar="PATH",
        help="project directory holding durable.json and .durable/ (default: .)",
    )
    parser.add_argument("--version", action="version", version=f"durable {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create durable.json and .durable/durable.db")

    status = sub.add_parser("status", help="show what the state file contains")
    status.add_argument("--json", action="store_true", help="machine-readable output")

    sub.add_parser("validate", help="parse durable.json and check the dependency graph")
    return parser


def cmd_init(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / config.DEFAULT_CONFIG_NAME
    if config_path.exists():
        print(f"keep    {config_path}")
    else:
        config_path.write_text(json.dumps(TEMPLATE, indent=2) + "\n", encoding="utf-8")
        print(f"create  {config_path}")

    db_path = store.default_db_path(root)
    existed = db_path.exists()
    conn = store.initialize(db_path, _now())
    conn.close()
    print(f"{'keep    ' if existed else 'create  '}{db_path}")
    print("\nNext: edit durable.json, then run `durable validate`.")
    return 0


def cmd_validate(root: Path) -> int:
    try:
        spec = config.load(root)
    except config.ConfigError as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1

    for name, workflow in sorted(spec.workflows.items()):
        print(f"workflow {name}  ({len(workflow.jobs)} job(s))")
        for job_name in workflow.order():
            job = workflow.jobs[job_name]
            needs = f"  <- {', '.join(job.needs)}" if job.needs else ""
            timeout = f"  timeout={job.timeout_s:g}s" if job.timeout_s else ""
            retries = f"  retry={job.retry.max_attempts}" if job.retry.max_attempts > 1 else ""
            print(f"  {job_name}{needs}{timeout}{retries}")
    return 0


def cmd_status(root: Path, as_json: bool) -> int:
    db_path = store.default_db_path(root)
    if not db_path.exists():
        print(f"no state at {db_path}; run `durable init` first", file=sys.stderr)
        return 1

    conn = store.connect(db_path)
    try:
        version = store.current_version(conn)
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("workflow", "job", "workflow_run", "job_run", "event")
        }
    finally:
        conn.close()

    if as_json:
        print(
            json.dumps(
                {
                    "root": str(root.resolve()),
                    "database": str(db_path),
                    "schema_version": version,
                    "counts": counts,
                },
                indent=2,
            )
        )
        return 0

    print(f"root      {root.resolve()}")
    print(f"database  {db_path.resolve()}")
    print(f"schema    v{version}")
    width = max(len(table) for table in counts) + 2
    for table, count in counts.items():
        print(f"{table:<{width}}{count}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root)

    if args.command == "init":
        return cmd_init(root)
    if args.command == "validate":
        return cmd_validate(root)
    if args.command == "status":
        return cmd_status(root, args.json)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
