# KARMA — Kubernetes Agent Reliability & Microservice Assessment

KARMA is a benchmarking framework that evaluates AI agents on real Kubernetes
microservice tasks. Each test case deploys a scenario into an ephemeral
namespace, instructs the agent to diagnose or remediate it, and scores the
outcome via oracle checks and an LLM judge.

The framework has two entrypoints:

- `orchestrator.py` — the command-line interface for running cases and
  workflows and for judging completed runs.
- `main.py` — the HTTP/SSE server backing the web UI.

---

## Quick start (CLI)

### Prerequisites

- Python 3.11+
- `kubectl` on `PATH` pointing at a reachable cluster
- A kubeconfig at `~/.kube/config` (or set `KUBECONFIG`)

### Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Agents

The agent is the system under test, selected with `--agent`. Registered agents
(see `karma/agents/registry.py`):

| Agent | Status | Notes |
|---|---|---|
| `claude_code` | **working** | `local` runs the host `claude` CLI; `docker` needs `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`) or `ANTHROPIC_API_KEY` |
| `codex` | **working** | `local` and `docker`; `docker` mounts `~/.codex/auth.json` (`--agent-auth-path`/`--agent-auth-dest`) or set `OPENAI_API_KEY` |
| `cli_runner`, `react` | **scaffold only** | `entrypoint.sh` calls a `run_agent.py` that does not exist — plug in your own implementation to use them |

### Run a single test case

```bash
python orchestrator.py run-case rabbitmq failover \
  --agent   claude_code \
  --sandbox local
```

`run-case` takes the service and case as positional arguments. Key flags:

| Flag | Default | Description |
|---|---|---|
| `--agent` | none | Agent ID from the registry (`claude_code`, `codex`, …) |
| `--sandbox` | `local` | `local` (host process) or `docker` (containerized) |
| `--agent-build` | off | Build the agent's Docker image before running (docker mode) |
| `--agent-auth-path` / `--agent-auth-dest` | none | Mount a host creds file into the agent container (e.g. `~/.codex/auth.json` → `/root/.codex/auth.json`) |
| `--param KEY=VALUE` | none | Case parameter override (repeatable; JSON-decoded) |
| `--timeout` | `900` | Agent timeout in seconds |
| `--runs-dir` | `runs` | Root directory for run artifacts |
| `--resources-dir` | `resources` | Override resource root |
| `--profile` | none | Named profile of default flags |
| `--output` | `text` | `text` or `json` |

Docker-sandbox example (build the image and mount Codex auth):

```bash
python orchestrator.py run-case demo configmap-update \
  --agent codex --sandbox docker --agent-build \
  --agent-auth-path ~/.codex/auth.json --agent-auth-dest /root/.codex/auth.json
```

### Run a workflow

```bash
python orchestrator.py run-workflow workflows/workflow-demo.yaml \
  --agent claude_code
```

Add `--dry-run` to resolve and print the normalized workflow without
executing it.

### Judge a completed run

```bash
python orchestrator.py judge runs/<run_id> --stage stage_1
```

Omit `--stage` to judge every stage in the run directory.

### Inspect the registry

```bash
python orchestrator.py info --agents --metrics
```

---

## Web UI

`main.py` serves a single-page web UI from `static/` at the server root.
Open `http://127.0.0.1:8080` after starting it. Tabs:

- **Cases** — browse services and cases (and adversary scenarios), inspect a
  case's prompt and parameters, then run it with an agent (stage events stream
  live) or as a **manual** run: set the scenario up, do the task by hand
  against the assigned namespaces, then submit for verification.
- **Workflow** — list workflow files and run them, or build a workflow
  stage-by-stage, validate the YAML, and run it inline; a jobs panel streams
  progress. "Customize" a saved workflow to edit it as a copy.
- **Results** — every run, live and historical: per-stage status with failure
  logs, the test score (judge), the regression sweep, and cross-run judge
  batches. Judge a single run, or "Judge all" to score every unscored run.

The UI is plain HTML/CSS/JS under `static/` (no build step).

## HTTP API

Start the server (host/port come from `KARMA_HOST` / `KARMA_PORT`):

```bash
python main.py
```

### `GET /health`

Returns `{"status": "ok"}` when the server is running.

### `POST /api/run`

Submit a run. Accepts a JSON body with either a single case or an inline
workflow YAML, and returns a `run_id`.

**Single case:**

```json
{
  "service": "rabbitmq",
  "case_name": "failover",
  "agent": "claude_code",
  "sandbox": "local"
}
```

**Inline workflow:**

```json
{
  "workflow_yaml": "<raw YAML string>",
  "agent": "claude_code"
}
```

Response: `{"run_id": "<id>"}` with HTTP 201.

### `GET /api/run/<run_id>/status`

Return status and metadata for a run, or 404 when unknown.

### `GET /api/run/<run_id>/stream`

Server-Sent Events stream of stage-completion events. Each event is a JSON
object; the stream ends with a `{"type": "done"}` event.

```
data: {"type": "stage_complete", "stage": {"stage_id": "stage_1", "status": "pass"}}
```

### `POST /api/run/<run_id>/cancel`

Request cancellation of a running job.

`POST /api/run` also accepts `{"workflow_path": "<path>"}` to run a workflow
file on disk by path.

### Catalog & listings

| Endpoint | Returns |
|---|---|
| `GET /api/services` | services with case counts + cluster status |
| `GET /api/cases` | cases grouped by service |
| `GET /api/cases/<service>/<case>` | case detail: prompt, params, contract, metrics |
| `GET /api/runs` | run history with mean judge scores |
| `GET /api/workflows` | workflow files with validity/stage count |
| `GET /api/jobs` | active job registry |
| `GET /api/agents`, `GET /api/metrics` | registered agents / metric plugins |

### Manual operator runs

| Endpoint | Purpose |
|---|---|
| `POST /api/manual/start` | begin a manual run (`{service, case_name, params?}`) |
| `GET /api/manual/<id>/status` | poll setup phase / verdict |
| `POST /api/manual/<id>/submit` | verify the operator's work (re-runnable) |
| `POST /api/manual/<id>/cleanup` | tear down proxy + namespaces |
| `POST /api/manual/<id>/adversary/deploy` · `/lift` | inject/lift a scenario (`{scenario}`) |

### Judge

| Endpoint | Purpose |
|---|---|
| `POST /api/judge` | judge a run dir synchronously (`{run_dir, stage_id?, model?}`) |
| `POST /api/judge/start` | async judge (`{target_type: run\|batch\|all, target_path, dry_run?}`); `all` scores every unscored run |
| `GET /api/judge/jobs`, `/jobs/<id>`, `/jobs/<id>/stream` | judge job list / status / SSE |
| `GET /api/judge/runs`, `/api/judge/batches` | runs and cross-run batches with scores |

### Tooling

| Endpoint | Purpose |
|---|---|
| `GET /api/cli/options`, `POST /api/cli/preview` | CLI command builder |
| `POST /api/workflow/import` | validate pasted workflow YAML |
| `GET /api/adversary/scenarios` | discoverable adversary scenarios |
| `GET /api/proxy/status` | kubectl-proxy status |

---

## Environment variables

All runtime tunables are read from `KARMA_*` environment variables at import
time (see `karma/settings.py`).

| Variable | Default | Description |
|---|---|---|
| `KARMA_ORACLE_TIMEOUT_SEC` | `120` | Seconds before an oracle check times out |
| `KARMA_COMMAND_TIMEOUT_SEC` | `120` | Seconds per precondition/adversary apply command |
| `KARMA_PRECONDITION_TIMEOUT_SEC` | `600` | Total precondition phase budget |
| `KARMA_RESOURCES_DIR` | `resources` | Root resources directory |
| `KARMA_RUNS_DIR` | `runs` | Root run-artifact directory |
| `KARMA_HOST` / `KARMA_PORT` | `127.0.0.1` / `8080` | HTTP server bind address |
| `KARMA_JUDGE_MODEL` | `gpt-4o` | Judge LLM model |
| `KARMA_JUDGE_API_KEY` | (or `OPENAI_API_KEY`) | Judge LLM API key |
| `KUBECONFIG` | `~/.kube/config` | Path to kubeconfig |

---

## Project layout

```
orchestrator.py    CLI entrypoint  -> karma.interfaces.cli.main
main.py            HTTP entrypoint -> karma.interfaces.http.server
karma/
  definitions/   case, workflow, prompt loading and validation
  environments/  Kubernetes namespace lifecycle
  metrics/       scoring metric plugins
  judge/         LLM judge client, rubric, and scoring
  adversary/     adversary injection deploy/lift lifecycle
  runtime/       stage and workflow loop, service API, manual operator mode
  transport/     kubectl proxy and agent bundle
  interfaces/    CLI and HTTP/SSE adapters
    http/        server, events (SSE hub), jobs, catalog, judging,
                 cli_preview
  judge/         engine + batch (cross-run) aggregation
  oracle.py      automated pass/fail verification
  evidence.py    snapshot collection and metric dispatch
  sandbox.py     local and Docker agent launch
  protocol.py    run-directory layout and artifact paths
  settings.py    environment-variable configuration
static/          web UI (no build step)
  index.html     app shell
  css/styles.css
  js/            api.js, app.js, views/{runner,workflow,judge,adversary}.js
tests/
  unit/          fast unit tests (no cluster required)
  integration/   end-to-end tests (require live cluster)
resources/       service case definitions and manifests
workflows/       example workflow YAML files
```
