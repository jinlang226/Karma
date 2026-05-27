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

### `GET /api/cases`, `GET /api/agents`, `GET /api/metrics`

List available cases (grouped by service), registered agents, and registered
metric plugins.

### `POST /api/judge`

Run the judge on a completed run directory. Body: `{"run_dir": "...", "stage_id": "...", "model": "..."}` (`stage_id` and `model` optional).

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
  runtime/       stage and workflow execution loop, service API
  transport/     kubectl proxy and agent bundle
  interfaces/    CLI and HTTP/SSE adapters
  oracle.py      automated pass/fail verification
  evidence.py    snapshot collection and metric dispatch
  sandbox.py     local and Docker agent launch
  protocol.py    run-directory layout and artifact paths
  settings.py    environment-variable configuration
tests/
  unit/          fast unit tests (no cluster required)
  integration/   end-to-end tests (require live cluster)
resources/       service case definitions and manifests
workflows/       example workflow YAML files
```
