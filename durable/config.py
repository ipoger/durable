"""Strict parser for ``durable.json``.

Everything is validated before it reaches the database, and every error carries
a JSON path so the operator can jump straight to the offending line. The parser
is deliberately hand-written: a schema library would add a dependency, and the
format is small enough that being explicit is cheaper than being generic.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

DEFAULT_CONFIG_NAME = "durable.json"
SUPPORTED_VERSION = 1

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_BACKOFFS = ("exponential", "fixed")

JOB_KEYS = {"command", "needs", "retry", "timeout_seconds", "continue_on_error"}
RETRY_KEYS = {"max", "backoff", "base_seconds"}
WORKFLOW_KEYS = {"jobs"}
TOP_KEYS = {"version", "workflows"}


class ConfigError(Exception):
    """A problem with the workflow file, addressed by JSON path."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


@dataclass(frozen=True)
class Retry:
    """How many attempts a job gets, and how long to wait between them."""

    max_attempts: int = 1
    backoff: str = "exponential"
    base_seconds: float = 1.0

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait before ``attempt`` (1-based, attempt 1 has no delay)."""

        if attempt <= 1:
            return 0.0
        if self.backoff == "fixed":
            return self.base_seconds
        return self.base_seconds * (2 ** (attempt - 2))


@dataclass(frozen=True)
class JobSpec:
    name: str
    command: str
    needs: Tuple[str, ...] = ()
    retry: Retry = field(default_factory=Retry)
    timeout_s: Optional[float] = None
    continue_on_error: bool = False


@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    jobs: Mapping[str, JobSpec]

    def order(self) -> List[str]:
        """Topological order, ties broken by name so output is deterministic."""

        remaining = {name: set(job.needs) for name, job in self.jobs.items()}
        ordered: List[str] = []
        while remaining:
            ready = sorted(name for name, needs in remaining.items() if not needs)
            if not ready:
                raise ConfigError(
                    f"workflows.{self.name}.jobs",
                    "dependency cycle among: " + ", ".join(sorted(remaining)),
                )
            for name in ready:
                ordered.append(name)
                del remaining[name]
            for needs in remaining.values():
                needs.difference_update(ready)
        return ordered


@dataclass(frozen=True)
class Spec:
    version: int
    workflows: Mapping[str, WorkflowSpec]


def load(root: Path, filename: str = DEFAULT_CONFIG_NAME) -> Spec:
    """Read and validate the workflow file under ``root``."""

    path = Path(root) / filename
    if not path.is_file():
        raise ConfigError(filename, "file not found; run `durable init` first")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(filename, f"invalid JSON at line {exc.lineno}: {exc.msg}") from exc
    return parse(raw)


def parse(raw: Any) -> Spec:
    """Validate an already-decoded document."""

    if not isinstance(raw, dict):
        raise ConfigError("$", "expected a JSON object")

    _reject_unknown(raw, TOP_KEYS, "$")

    version = raw.get("version")
    if version != SUPPORTED_VERSION:
        raise ConfigError(
            "version", f"expected {SUPPORTED_VERSION}, got {version!r}"
        )

    workflows_raw = raw.get("workflows")
    if not isinstance(workflows_raw, dict) or not workflows_raw:
        raise ConfigError("workflows", "expected a non-empty object")

    workflows: Dict[str, WorkflowSpec] = {}
    for name, body in workflows_raw.items():
        where = f"workflows.{name}"
        _check_name(name, where)
        if not isinstance(body, dict):
            raise ConfigError(where, "expected an object")
        _reject_unknown(body, WORKFLOW_KEYS, where)
        jobs_raw = body.get("jobs")
        if not isinstance(jobs_raw, dict) or not jobs_raw:
            raise ConfigError(f"{where}.jobs", "expected a non-empty object")

        jobs: Dict[str, JobSpec] = {}
        for job_name, job_body in jobs_raw.items():
            job = _parse_job(job_name, job_body, f"{where}.jobs.{job_name}")
            jobs[job_name] = job

        for job in jobs.values():
            for index, dep in enumerate(job.needs):
                if dep == job.name:
                    raise ConfigError(
                        f"{where}.jobs.{job.name}.needs[{index}]",
                        "a job cannot depend on itself",
                    )
                if dep not in jobs:
                    raise ConfigError(
                        f"{where}.jobs.{job.name}.needs[{index}]",
                        f"unknown job {dep!r}",
                    )

        workflow = WorkflowSpec(name=name, jobs=jobs)
        workflow.order()  # raises ConfigError on a cycle
        workflows[name] = workflow

    return Spec(version=version, workflows=workflows)


def _parse_job(name: str, body: Any, where: str) -> JobSpec:
    _check_name(name, where)
    if not isinstance(body, dict):
        raise ConfigError(where, "expected an object")
    _reject_unknown(body, JOB_KEYS, where)

    command = body.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ConfigError(f"{where}.command", "expected a non-empty string")

    needs_raw = body.get("needs", [])
    if not isinstance(needs_raw, list) or any(not isinstance(x, str) for x in needs_raw):
        raise ConfigError(f"{where}.needs", "expected an array of job names")
    if len(set(needs_raw)) != len(needs_raw):
        raise ConfigError(f"{where}.needs", "duplicate entries")

    retry = _parse_retry(body.get("retry"), f"{where}.retry")

    timeout = body.get("timeout_seconds")
    if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
        raise ConfigError(f"{where}.timeout_seconds", "expected a positive number")

    continue_on_error = body.get("continue_on_error", False)
    if not isinstance(continue_on_error, bool):
        raise ConfigError(f"{where}.continue_on_error", "expected a boolean")

    return JobSpec(
        name=name,
        command=command,
        needs=tuple(needs_raw),
        retry=retry,
        timeout_s=float(timeout) if timeout is not None else None,
        continue_on_error=continue_on_error,
    )


def _parse_retry(raw: Any, where: str) -> Retry:
    if raw is None:
        return Retry()
    if not isinstance(raw, dict):
        raise ConfigError(where, "expected an object")
    _reject_unknown(raw, RETRY_KEYS, where)

    max_attempts = raw.get("max", 1)
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise ConfigError(f"{where}.max", "expected an integer >= 1")

    backoff = raw.get("backoff", "exponential")
    if backoff not in _BACKOFFS:
        raise ConfigError(f"{where}.backoff", f"expected one of {list(_BACKOFFS)}")

    base = raw.get("base_seconds", 1.0)
    if not isinstance(base, (int, float)) or isinstance(base, bool) or base <= 0:
        raise ConfigError(f"{where}.base_seconds", "expected a positive number")

    return Retry(max_attempts=max_attempts, backoff=backoff, base_seconds=float(base))


def _check_name(name: Any, where: str) -> None:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ConfigError(where, "expected 1-64 chars: letters, digits, '-' or '_'")


def _reject_unknown(body: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise ConfigError(where, "unknown field(s): " + ", ".join(unknown))
