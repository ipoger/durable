# durable — design

> Status: M0 (skeleton). This document is the contract; code follows it, not the
> other way around. Changes to the state machine or the data model require an
> edit here first.

## 1. Problem

Personal automation and small-team pipelines live in shell scripts, cron and
Makefiles. Those tools are fine until something fails at 3am:

- a step dies halfway and nothing retries it;
- the machine reboots and you cannot tell which steps ran;
- steps have dependencies but the script only encodes order;
- logs are interleaved in one terminal buffer and lost on rotation.

Existing answers are heavy: Airflow and Dagster want a scheduler service,
Celery wants a broker, Temporal wants a cluster. **durable** is the missing
middle: one process, one SQLite file, a JSON workflow, and enough state to
answer "what happened, and what should happen next" after a crash.

## 2. Non-goals (v1)

Explicitly **not** in scope. Each of these is a place where the project would
stop being small:

- **No distributed workers.** One process, local subprocesses, one machine.
- **No remote API.** No HTTP server, no auth, no multi-user.
- **No plugin runtime.** Commands are shell strings, not Python callables.
- **No exactly-once semantics.** See §5 — we promise at-least-once and say so.
- **No retry of side effects we cannot see.** A command that half-wrote a file
  is the operator's problem; we only guarantee the command runs again.
- **No DAG editor / web UI.** The workflow file is the interface.

## 3. Concepts

| Concept | Meaning |
| --- | --- |
| **Workflow** | A named DAG of jobs, declared in `durable.json`. |
| **Job** | One command plus its policy: dependencies, retries, timeout. |
| **Workflow run** | One attempt to execute a whole workflow. |
| **Job run** | One *attempt* of one job inside a workflow run. |
| **Event** | An append-only audit record (`job.started`, `job.failed`, …). |

A `job_run` row is created per attempt, not per job: attempt 1 failed and
attempt 2 succeeded are two rows, and the history survives.

## 4. State machine

```
                       ┌──────────── cancelled ◄────────── any non-terminal
                       │
pending ──► ready ──► running ──► succeeded
              ▲          │
              │          ├──► failed            (attempts exhausted)
              │          ├──► retrying ──► ready (attempts remain)
              │          └──► interrupted ──► ready | failed
              │
              └── (dependency succeeded / retry scheduled)

pending ──► skipped        (a dependency failed or was skipped)
```

Rules that the store enforces:

1. Only the transitions in `durable.model.ALLOWED` are legal; anything else
   raises. The table is the single source of truth and is unit-tested.
2. `succeeded`, `failed`, `skipped`, `cancelled` are terminal for a job run.
   `interrupted` is *not* terminal — it is a recoverable crash marker.
3. A job may only enter `ready` when every dependency is `succeeded`, or when
   the dependency failed and the job declares `continue_on_error`.

## 5. Execution semantics: at-least-once

This is the most important sentence in the document:

> **A job may run more than once. It will never be silently skipped.**

Consequences the operator must design around:

- Commands should be **idempotent** where possible (`mkdir -p`, `--force`,
  upsert instead of insert).
- After a crash, any job that was `running` is marked `interrupted` and becomes
  eligible for another attempt. We cannot know whether the side effect landed.
- `attempt` is monotonic per `(workflow_run, job)`; the log path includes it, so
  a retry never overwrites the previous attempt's output.
- `max_attempts = 1` means "no retry", not "run once ever".

## 6. Data model

SQLite, WAL mode, `foreign_keys=ON`, `busy_timeout=5000`. One file per project
at `.durable/durable.db`. Schema version is stored in `schema_version` so that
M4 can migrate without guessing.

```
workflow(id, name UNIQUE, spec_json, created_at, updated_at)
job(id, workflow_id → workflow, name, command, depends_on JSON,
    retry_max, backoff_base_s, timeout_s, continue_on_error,
    UNIQUE(workflow_id, name))
workflow_run(id, workflow_id → workflow, state, trigger, created_at, finished_at)
job_run(id, workflow_run_id → workflow_run, job_id → job, attempt, state,
        exit_code, started_at, finished_at, heartbeat_at, log_path, error,
        UNIQUE(workflow_run_id, job_id, attempt))
event(id, at, workflow_run_id, job_run_id, type, payload_json)
```

Design notes:

- **`spec_json` is kept verbatim** next to the normalised rows. When the
  config parser gains a field, old runs still show what was declared.
- **`depends_on` is JSON text.** SQLite has no array type; a join table would
  be more "correct" but the DAG is read as a whole and never queried by edge.
- **Timestamps are UTC ISO-8601 strings.** Sortable, human-readable in `sqlite3`,
  and good enough until we need sub-millisecond ordering.
- **Logs live on disk**, not in the database. The DB stores the path. A 200 MB
  log must not bloat the state file.

## 7. Failure and recovery

| Situation | Behaviour |
| --- | --- |
| Command exits non-zero | `failed`; retry if attempts remain |
| Timeout | process group killed, `failed` with `error=timeout` |
| Process crashes mid-run | on next start, `running` rows with a stale heartbeat → `interrupted` → retry |
| SQLite locked | retry with `busy_timeout`; never busy-loop |
| Disk full while writing a log | job fails with `error=log_write`; DB write still attempted |
| SIGINT / SIGTERM | stop scheduling, let running jobs finish or hit their timeout |

Staleness is defined by `heartbeat_at`: the supervisor touches it every
`heartbeat_interval` (default 5s); a row whose heartbeat is older than
`3 × interval` is considered orphaned. This is deliberately simple — no
distributed leases, no clock agreement between machines.

## 8. Configuration format

`durable.json` at the project root. JSON, not YAML/TOML, only because Python
3.9 has no `tomllib` and the project accepts zero runtime dependencies. A
`durable.toml` reader can be added later without changing the model.

```json
{
  "version": 1,
  "workflows": {
    "nightly": {
      "jobs": {
        "fetch": { "command": "./scripts/fetch.sh" },
        "build": {
          "command": "make build",
          "needs": ["fetch"],
          "retry": { "max": 3, "backoff": "exponential", "base_seconds": 2 },
          "timeout_seconds": 600
        }
      }
    }
  }
}
```

Validation is strict and happens before anything is written: unknown fields,
missing dependencies, duplicate job names and cycles are all rejected with a
JSON path in the error (`workflows.nightly.jobs.build.needs[0]`).

## 9. Milestones

Each milestone ends with a working command, a green CI, and a PR.

| # | Scope | Done when | Est. |
| --- | --- | --- | --- |
| **M0** | Design doc, package layout, schema, config parser, `init` / `status` / `validate` | `durable init` creates a DB; `validate` rejects a cycle | ~700 |
| **M1** | Store layer: repository functions, transitions, events | Illegal transition raises; event log is queryable | ~600 |
| **M2** | Executor: subprocess, timeout, log capture, retry with backoff | A flaky command succeeds on attempt 2 | ~700 |
| **M3** | Scheduler: dependency resolution, parallel workers, skip-on-failure | A 3-job DAG runs in topological order | ~600 |
| **M4** | Crash recovery, heartbeat, idempotency keys, migrations | `kill -9` mid-run recovers on restart | ~500 |
| **M5** | Triggers (interval/cron), `logs` / `retry` / `cancel`, docs, v1.0.0 | End-to-end demo in the README | ~700 |

Roughly 3,800 lines including tests. The order is deliberate: **state before
execution, execution before scheduling, scheduling before recovery.** Recovering
from a crash is impossible to test until state and scheduling are honest.

## 10. Open questions

Tracked here so they are decisions, not accidents:

- **Trigger syntax.** `every: "5m"` is easy; cron is familiar. Likely both,
  interval first.
- **Log retention.** Keep last N attempts? Compress? Defer to M5.
- **`continue_on_error` on workflows.** Per-job today. A workflow-level
  "always run cleanup" is probably a job with `needs: []` and a special mode.
- **Windows.** `subprocess` process-group killing differs. v1 targets POSIX and
  says so in the README.

## 11. Why not X

- **Why not just use Airflow?** It is a scheduler service with a database, a web
  UI and an opinion about deployment. The target user has a laptop.
- **Why not a Makefile?** No retries, no per-attempt logs, no state after a
  crash, and `.PHONY` is not a dependency graph.
- **Why SQLite?** It is in the standard library, it is a single file, and it
  survives `kill -9` better than a JSON file rewritten in place.
