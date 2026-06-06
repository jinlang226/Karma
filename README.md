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

### Run a single test case

```bash
python orchestrator.py run-case rabbitmq-experiments failover \
  --agent   cli_runner \
  --sandbox local
```

`run-case` takes the service and case as positional arguments. Key flags:

| Flag | Default | Description |
|---|---|---|
| `--agent` | none | Agent ID from the agent registry (`cli_runner`, `react`) |
| `--sandbox` | `local` | `local` or `docker` |
| `--param KEY=VALUE` | none | Case parameter override (repeatable; JSON-decoded) |
| `--timeout` | `900` | Agent timeout in seconds |
| `--runs-dir` | `runs` | Root directory for run artifacts |
| `--resources-dir` | `resources` | Override resource root |
| `--profile` | none | Named profile of default flags |
| `--output` | `text` | `text` or `json` |

### Run a workflow

```bash
python orchestrator.py run-workflow workflows/workflow-demo.yaml \
  --agent cli_runner
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
Open `http://127.0.0.1:8080` after starting it. Four tabs:

- **Runner** — browse services and cases, inspect a case's prompt and
  parameters, then run it with an agent (stage events stream live) or as a
  **manual** run: set the scenario up, do the task by hand against the
  assigned namespaces, then submit for verification. Includes a command
  builder that renders the equivalent CLI.
- **Workflow** — list workflow files and run them, or build a workflow
  stage-by-stage, validate the YAML, and run it inline; a jobs panel
  streams progress.
- **Judge** — list runs and batches with judge scores and trigger a judge
  (or dry run), with progress streamed to a log.
- **Adversary** — list adversary scenarios and inject or lift one against a
  live manual run.

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
  "service": "rabbitmq-experiments",
  "case_name": "failover",
  "agent": "cli_runner",
  "sandbox": "local"
}
```

**Inline workflow:**

```json
{
  "workflow_yaml": "<raw YAML string>",
  "agent": "cli_runner"
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
| `POST /api/judge/start` | async judge (`{target_type: run\|batch, target_path, dry_run?}`) |
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
