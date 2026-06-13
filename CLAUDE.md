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
webui/          web UI served at "/" (plain HTML/CSS/JS, no build step)
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
- The web UI in `webui/` talks only to the `/api/*` endpoints. Each view
  registers itself via `KARMA.registerView`; add a view by dropping a file
  under `webui/js/views/` and a `<script>` tag in `index.html`.

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
python orchestrator.py run-case rabbitmq failover \
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

`KARMA_RESOURCES_DIR` (default `cases`), `KARMA_RUNS_DIR` (`runs`),
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

The codebase is **internally complete, unit/spec-verified, and runnable
end-to-end with a real agent** (validated 2026-06-11..12). The **web UI and full
HTTP surface are built** (Cases/Runner, Workflow, Results+Judge, and Adversary
views; manual-operator runs; cross-run judge batches).

**Real-agent status:** ~62/79 cases pass with `claude_code`/sonnet; the 4 deep
porting bugs are fixed and verified. The judge is run-level + objective (score =
% stages passed; the LLM only adjudicates regression-sweep false positives;
writes `runs/<id>/judge.json` + `judge.log`).

**Agents** (`karma/agents/`, registry in `registry.py`):
- `claude_code` — real; **local** sandbox = host `claude` subprocess (proven).
  **docker** sandbox needs `CLAUDE_CODE_OAUTH_TOKEN` (run `claude setup-token`)
  or `ANTHROPIC_API_KEY` in the env — the host OAuth login is not forwarded.
- `codex` — real; works in **local** sandbox; **docker** mounts
  `~/.codex/auth.json` via `--agent-auth-path/--agent-auth-dest` (or set
  `OPENAI_API_KEY`). (CLI v0.139, model gpt-5.5.)
- `cli_runner`, `react` — **empty scaffolds**: their `entrypoint.sh` calls a
  `run_agent.py` that does not exist. Useful only as a cheap no-LLM precondition
  check (the agent dies, but setup runs first). Plug in `run_agent.py` to use.

**Still requires external setup:** a reachable Kubernetes cluster (Docker +
`kind`) — a run creates namespaces as its first step. Manual-operator mode needs
no agent (a human does the task).

**Local infra (this machine):** kind clusters `kind` (kubeconfig `/tmp/mt/kc-a`)
and `karma-b` (`/tmp/mt/kc-b`); pin `KUBECONFIG` per run to parallelize. The HTTP
server (`main.py`) imports modules at startup, so **any backend `.py` change
needs a server restart**; static JS/CSS is served from disk (browser refresh).

## Conventions

- **Commits:** Conventional Commits — `<type>(<scope>): <short imperative
  description>`, with a prose body explaining *why*, followed by a
  `Files changed:` footer listing each touched file (one per line, 2-space
  indent). Match the existing `git log` style on the `refactor` branch.
- **Docstrings:** every function has a docstring; every file has a header/overview
  comment. Keep docstrings short enough not to truncate on a normal terminal
  width — prefer concise one-to-three-line summaries.

## WORK LOG — real-agent validation & hardening (2026-06-11)

**Status: largely complete — see `FINAL_REPORT.md` for the full writeup.** The
plan below is the historical task list; the headline outcomes are: 58/79 (73%)
real-agent pass rate, 2 framework regressions fixed (`required_roles: []`
namespace + per-command timeout), 3 mongodb case bugs fixed, the adversary
capstone + regression sweep validated, agent-log/retry/judge/UI features
delivered. Remaining: 4 deep rabbitmq/cockroachdb porting bugs + nginx multi-node
(reported, not fixed) and the adversary-cases-in-Cases-tab follow-up.

### Original plan (added 2026-06-11)

Hardening sequence on the `refactor` branch. Every test/oracle/precondition fix is
referenced against the OLD repo at `../kubernetes-microservice-benchmark` (it has
`app/`, 94 cases). Validation runs live in `runs/` (real `claude_code`/**sonnet** agent
runs). Driver: `/tmp/mt/driver.py` (env `MT_RUNS`/`MT_RESULTS`; runs
`orchestrator.py run-case <svc> <case> --agent claude_code --sandbox local --timeout N`).
Framework verdict so far: CLEAN — the only framework bug was the `required_roles: []`
namespace regression (fixed, 2 commits).

**Locked decisions:** retry is a WORKFLOW-level, stage-agnostic setting (one
`max_attempts`, default 1, applies to every stage), retries on oracle **fail**, each
attempt re-runs the whole stage with the FULL per-stage time limit. Keep the agent time
limit MODEST (do not bloat to 30 min) — fix the verbose agent log FIRST, then reason
through time-up failures via the log. Judge: score **0–100.0, 0.1 precision**; input =
stage config + prompt + **agent log** + **regression-sweep result**; run on ALL runs incl.
failed. Prioritize SPEED (parallel clusters OK; the monthly spend limit auto-stops the
agent — handle/resume if hit). Adversary capstone ≈ **10 stages**, 1–2 injections.

**PHASE A — code only, commit each (no cluster/agent):**
- A1 (7) agent.log: `karma/agents/claude_code/entrypoint.sh` → `--verbose`/
  `--output-format stream-json`; stream full turn-by-turn to agent.log, extract final
  result for submit.txt. (Today agent.log is 0 bytes everywhere; agent K8s actions ARE in
  `kubectl_log.jsonl`.)
- A2 (6-analysis) old-repo diff of the 10 test bugs → regression vs pre-existing + correct version.
- A3 (1) retry: `_should_retry` (workflow.py) include `"fail"`; workflow-level `max_attempts`
  through `run_workflow`→loop; add a Run Config UI control next to agent/sandbox.
- A4 (3) fix judge "target path not found" (Results-page Judge button → judging endpoint path).
- A5 (4) judge upgrades: 100.0/0.1 score (rubric+scoring in `karma/judge/`); input += agent log
  + regression sweep; run-on-failed.
- A6 (2) UI: (a) Results page sometimes all-white on launch — fix mount/background; (b) Workflow
  builder param boxes — flexible grid, width by count, max 4/row (today fixed at 4 even for 2).

**PHASE B — cluster + agent (speed-first, parallel OK):**
- B1 (6+7+5) per test bug: apply old-repo-referenced fix → real-agent re-run → confirm fixed AND
  agent.log populated (verbose). Revert if it turns out not a bug.
- B2 (5) re-run time-up cases; diagnose via verbose log (stuck/looping vs genuinely needs time).
- B3 (8) build + run adversary ~10-stage workflow (real agent) → verifies adversary inject/lift,
  regression sweep, and full judge pipeline.
- B4 final report + judge ALL runs.

**KEY DATA — the 10 test bugs (all errored ≥3×, real):**
- precondition: `cockroachdb/certificate-rotation` (waits `phase=Running` then execs `cockroach
  init` → "container not found db"; fix `--for=condition=Ready`); `mongodb/deploy` (verify expects
  running pods pre-agent); `mongodb/external-access-horizons` (unsubstituted `$node_ip` in pod
  manifest); `mongodb/statefulset-customization` (verifies pods right after deleting them, no
  recreate wait); `rabbitmq/blue_green_migration` + `rabbitmq/manual_backup_restore` ("no matching
  resources found"); `rabbitmq/manual_tls_rotation` + `nginx/rate_limit_replica_hard` (120s
  per-command cap too short for cluster startup / replica scale-up).
- oracle: `nginx/renew_tls_secret` (oracle aborts on its own missing `INGRESS_NODE_IP`/
  `INGRESS_HTTPS_PORT` env); `nginx/otel_log_format` (needs traffic generation).
- 18 agent fails = 7 time-up (cockroachdb/generate-cert, elasticsearch/{full-restart-upgrade-ha-hard,
  safe-downscale-with-shard-migration}, mongodb/{password-rotation, version-upgrade-hard},
  rabbitmq/manual_skip_upgrade, ray/deploy_cluster) + 11 submitted-but-wrong.

**A2 FINDINGS (old-repo diff):** OLD repo uses a DIFFERENT schema (flat
`preOperationCommands`/`verificationCommands`/`detailedInstructions`), NOT the new
`preconditionUnits` (probe/apply/verify). So bugs are mostly PORTING errors in the new
probe/apply/verify split, to be fixed by understanding each case's intent then verified
with a real agent in B1. My earlier surface diagnoses were partly WRONG -- re-verify each:
- mongodb/deploy: new `verify` expects Running pods its `apply` never creates (apply only
  makes the namespace) -> porting bug; determine if agent is meant to deploy (fix verify)
  or precondition should deploy (fix apply) from old detailedInstructions.
- mongodb/external-access-horizons: `$node_ip` code IDENTICAL in old -> NOT the bug; real
  cause unknown, re-investigate in B1.
- cockroachdb/certificate-rotation: `phase=Running`-before-exec is SAME in old (+ 30x retry
  exec loop) -> NOT a clean regression; real cause likely pod/db container not becoming
  Ready; --for=condition=Ready fix UNCONFIRMED.
- nginx/rate_limit_replica_hard: 120s timeouts SAME in old -> too-short timeout, not a
  regression; may just need a longer timeout_sec (or fresh-cluster image-pull slowness).
- rabbitmq/blue_green_migration: new multi-namespace (BENCH_NS_SOURCE/TARGET) rollout-status
  on blue/green statefulset that isn't present -> porting gap in the new structure.
Every fix MUST be real-agent verified in B1; revert if not actually a bug.
