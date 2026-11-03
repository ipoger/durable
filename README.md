# durable

[![CI](https://github.com/ipoger/durable/actions/workflows/ci.yml/badge.svg)](https://github.com/ipoger/durable/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)

**A small, durable task orchestrator.** One process, one SQLite file, a JSON
workflow: enough state to answer *what ran, what failed, and what should happen
next* after a crash.

> **Status: M0 — skeleton.** Config parsing, the state machine, the schema and
> `init` / `validate` / `status` work today. Execution arrives in M2; see the
> [roadmap](#roadmap). Star the repo if you want to follow along.

## Why

Personal automation lives in cron, shell scripts and Makefiles. Those are fine
until a step dies at 3am:

- nothing retries it, and nobody knows which steps ran;
- the machine reboots and the state is gone;
- dependencies are encoded as line order, not as a graph;
- logs from three parallel steps are interleaved in one buffer.

Airflow, Dagster and Temporal solve this for clusters. `durable` aims at the
laptop: no broker, no server, no daemon to babysit.

## Quick start

```console
$ durable init
create  /work/durable.json
create  /work/.durable/durable.db

$ durable validate
workflow example  (2 job(s))
  hello
  goodbye  <- hello

$ durable status
root      /work
database  /work/.durable/durable.db
schema    v1
workflow      0
job           0
workflow_run  0
job_run       0
event         0
```

No dependencies beyond Python 3.9+:

```sh
git clone https://github.com/ipoger/durable.git
cd durable
python3 -m durable --root /path/to/project init
```

## The workflow file

`durable.json` sits at the project root. JSON is a deliberate compromise: the
project has zero runtime dependencies and Python 3.9 has no `tomllib`.

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

Validation is strict and reports a JSON path for every problem, before anything
is written to the database:

```console
$ durable validate
invalid: workflows.nightly.jobs.build.needs[0]: unknown job 'fetxh'
```

## The model in one picture

```
pending ──► ready ──► running ──► succeeded
              ▲          │
              │          ├──► failed            (attempts exhausted)
              │          ├──► retrying ──► ready (attempts remain)
              │          └──► interrupted ──► ready | failed
              │
              └── (dependency succeeded / retry scheduled)

pending ──► skipped        (a dependency failed)
```

**Execution is at-least-once.** A job may run twice after a crash; it is never
silently skipped. Commands should be idempotent. This is the most important
sentence in the documentation, so it is also the first one in
[`docs/design.md`](docs/design.md).

## Non-goals (v1)

- no distributed workers, no remote API, no multi-user
- no exactly-once semantics
- no plugin runtime — commands are shell strings
- no web UI; the workflow file is the interface

## Roadmap

- [x] **M0** — design doc, schema, config parser, `init` / `validate` / `status`
- [ ] **M1** — store layer: repositories, transitions, event log
- [ ] **M2** — executor: subprocess, timeout, log capture, retry with backoff
- [ ] **M3** — scheduler: dependency resolution, parallel workers, skip-on-failure
- [ ] **M4** — crash recovery: heartbeat, interrupted runs, migrations
- [ ] **M5** — triggers, `logs` / `retry` / `cancel`, docs, v1.0.0

Each milestone ends with a working command and a green CI. The order is
deliberate: state before execution, execution before scheduling, scheduling
before recovery.

## Development

```sh
python3 -m unittest discover -s tests -t . -v
```

49 tests, standard library only. The design contract lives in
[`docs/design.md`](docs/design.md); changes to the state machine or the data
model go there first.

## License

[MIT](LICENSE) © 2026 ipoger
