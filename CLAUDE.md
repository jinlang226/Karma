# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## Project Overview

**KARMA** (Kubernetes Agent Reliability & Microservice Assessment) is a
benchmarking framework that evaluates AI agents on real Kubernetes microservice
tasks. Each test case deploys a scenario into ephemeral namespaces, instructs an
agent (a Docker container) to diagnose or remediate it, optionally injects
adversary disruptions, and scores the outcome via automated oracle checks, metric
plugins, and an LLM-as-judge.

- Package: `karma`, version `0.2.0`
- Python 3.11+ (the local `.venv` runs 3.14)
- Dependencies: Flask, Pydantic v2, PyYAML, OpenAI
- Runtime prerequisites for a real run: `kubectl` on `PATH` pointing at a
  reachable cluster, and a kubeconfig at `~/.kube/config` (or `KUBECONFIG`).

This repo is the product of a refactor from the old monolithic
`kubernetes-microservice-benchmark` into a layered `karma/` package. Scope note:
**KARMA is Kubernetes-only by design — there is no plan to extend it to a
bash/Linux environment.** Do not add that abstraction.

## Architecture

The codebase is deliberately layered; respect the dependency direction when
editing.

```
orchestrator.py    CLI entrypoint  -> karma.interfaces.cli.main
main.py            HTTP entrypoint -> karma.interfaces.http.server
karma/
  definitions/   case/workflow/prompt loading, Pydantic validation, normalization
  environments/  Kubernetes namespace lifecycle (provider registry + k8s.py)
  transport/     kubectl proxy daemon + agent credential bundle (transport/k8s/)
  adversary/     adversary injection deploy/lift/report lifecycle
  sandbox.py     local and Docker agent launch + container lifecycle
  runtime/       stage + workflow execution loop, public service API
  metrics/       scoring metric plugins (registry + dispatch)
  judge/         LLM judge client, input builder, rubric, scoring
  oracle.py      automated pass/fail verification + regression sweep
  evidence.py    kubectl-log snapshot collection, trace facts, metric dispatch
  protocol.py    run-directory layout and artifact path helpers
  settings.py    KARMA_* environment-variable configuration (singleton)
  interfaces/    CLI (cli/) and HTTP/SSE (http/) adapter layers
    http/        server (routes), events (reconnectable SSE hub), jobs
                 (run submission), catalog (browse/listing reads), judging
                 (async judge jobs + cross-run batches), cli_preview
  runtime/manual.py  interactive operator run mode (start/submit/cleanup)
  judge/batch.py     cross-run batch evaluation (mean experiment score)
static/          web UI served at "/" (plain HTML/CSS/JS, no build step)
  index.html · css/styles.css · js/{api,app}.js · js/views/*.js
```

**Layering rules (keep these intact):**

- `definitions/`, `oracle.py`, `evidence.py`, and `protocol.py` do **not** import
  from `runtime.*`. They are pure data/logic consumed by the runtime, not the
  other way around.
- `runtime/` is the single orchestration core. Both adapters in `interfaces/`
  (CLI and HTTP) consume `runtime.service`; they do not duplicate orchestration.
- All run-directory paths come from `protocol.py`. Do not hardcode path strings
  elsewhere.
- All runtime tunables come from `settings.py` (read from `KARMA_*` env vars at
  import time). Do not read env vars ad hoc.
- Direct `kubectl` invocation is confined to `karma/environments/k8s.py` and
  `karma/transport/k8s/backend.py`. Keep it there.
- `interfaces/http/server.py` stays thin: routes call into `jobs`, `catalog`,
  `judging`, `cli_preview`, `events`, or `runtime.*` and serialize the result.
  All run/judge progress streams through the single `events.hub`.
- The web UI in `static/` talks only to the `/api/*` endpoints. Each view
  registers itself via `KARMA.registerView`; add a view by dropping a file
  under `static/js/views/` and a `<script>` tag in `index.html`.

**Stage execution order** (see `karma/runtime/case.py`): create stage dir →
launch kubectl proxy → bind namespace roles + create namespaces → run
preconditions → plant decoys → adversary deploy → write agent bundle
(kubeconfig + env) → render/write prompt → launch agent → poll for `submit.txt`
or timeout → terminate agent → collect evidence → run oracle → adversary lift →
write stage metadata → tear down proxy + clean up namespaces.

**Run-directory layout** is owned by `protocol.py`:
`runs/{run_id}/{run.json, workflow_state.json, bundle/, stages/{stage_id}/...}`.

## Common Commands

Use the project virtualenv at `.venv` (Python 3.14, pytest 9 installed).

### Run a single test case (CLI)

```bash
python orchestrator.py run-case rabbitmq-experiments failover \
  --agent cli_runner --sandbox local
```

Key `run-case` flags: `--agent`, `--sandbox {local,docker}`, `--param KEY=VALUE`
(repeatable, JSON-decoded), `--timeout` (default 900), `--runs-dir`,
`--resources-dir`, `--profile`, `--output {text,json}`.

### Run a workflow

```bash
python orchestrator.py run-workflow workflows/workflow-demo.yaml --agent cli_runner
# add --dry-run to resolve + print the normalized workflow without executing
```

Example workflows live in `workflows/` (`workflow-demo.yaml`,
`workflow-demo-adversary.yaml`, `rabbitmq-upgrade-tls-migration-a-to-b.yaml`).

### Judge a completed run / inspect the registry

```bash
python orchestrator.py judge runs/<run_id> --stage stage_1   # omit --stage to judge all
python orchestrator.py info --agents --metrics
```

### HTTP server

```bash
python main.py   # binds KARMA_HOST:KARMA_PORT (default 127.0.0.1:8080)
```

Endpoints: `GET /health`, `POST /api/run`, `GET /api/run/<id>/status`,
`GET /api/run/<id>/stream` (SSE), `POST /api/run/<id>/cancel`, `GET /api/cases`,
`GET /api/agents`, `GET /api/metrics`, `POST /api/judge`.

### Key environment variables (see `karma/settings.py`)

`KARMA_RESOURCES_DIR` (default `resources`), `KARMA_RUNS_DIR` (`runs`),
`KARMA_HOST`/`KARMA_PORT`, `KARMA_JUDGE_MODEL` (`gpt-4o`),
`KARMA_JUDGE_API_KEY` (falls back to `OPENAI_API_KEY`),
`KARMA_ORACLE_TIMEOUT_SEC` (120), `KARMA_COMMAND_TIMEOUT_SEC` (120),
`KARMA_PRECONDITION_TIMEOUT_SEC` (600), `KUBECONFIG`.

## Testing

There are two distinct test locations — know the difference:

- **`tests/`** (committed): the project's own suite, split into `tests/unit/`
  (fast, no cluster) and `tests/integration/` (require a live cluster).
  `pyproject.toml` sets `testpaths = ["tests"]`.
- **`audit_tests/` and `spec_tests/`** (intentionally **uncommitted**): a large
  audit + spec verification suite (all passing, no cluster required) written to
  validate the refactor against the old codebase's behavior. These are kept out
  of git on purpose — do not `git add` them. The committed `tests/unit/` suite
  plus these run green together (`pytest tests/unit audit_tests spec_tests`).

```bash
# Run the audit + spec verification suites (no cluster needed)
.venv/bin/python -m pytest audit_tests/ spec_tests/ -q

# Run the committed unit tests
.venv/bin/python -m pytest tests/unit -q

# Integration tests require a reachable Kubernetes cluster
.venv/bin/python -m pytest tests/integration -q
```

## Current State / Known Gaps

The codebase is **internally complete and fully unit/spec-verified**, and the
**web UI and full HTTP surface are now built** (Runner, Workflow, Judge, and
Adversary views over the `/api/*` endpoints; manual-operator runs; cross-run
judge batches). It is still **not runnable end-to-end** without two pieces of
external setup:

1. **A Kubernetes cluster.** A real run creates namespaces in a cluster as its
   first step. Locally this needs Docker running + a `kind` cluster
   (`open -a Docker` → `kind create cluster` → `kubectl cluster-info`). Without
   one, the UI loads and all read-only endpoints work; the cluster banner shows
   "unreachable" and a run fails at namespace creation.
2. **A real agent.** The registered agents in `karma/agents/` (`cli_runner`,
   `react`) are scaffolding only — Dockerfile + entrypoint + prompt assets. The
   `react` entrypoint references a `run_agent.py` that does not exist. KARMA
   delivers the harness; the agent implementation is plugged in by the user.
   (Manual-operator mode needs no agent — a human does the task.)

## Conventions

- **Commits:** Conventional Commits — `<type>(<scope>): <short imperative
  description>`, with a prose body explaining *why*, followed by a
  `Files changed:` footer listing each touched file (one per line, 2-space
  indent). Match the existing `git log` style on the `refactor` branch.
- **Docstrings:** every function has a docstring; every file has a header/overview
  comment. Keep docstrings short enough not to truncate on a normal terminal
  width — prefer concise one-to-three-line summaries.
