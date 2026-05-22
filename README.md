# KARMA — Kubernetes Agent Reliability & Microservice Assessment

KARMA is a benchmarking framework that evaluates AI agents on real Kubernetes
microservice tasks. Each test case deploys a scenario into an ephemeral
namespace, instructs the agent to diagnose or remediate it, and scores the
outcome via oracle checks and an LLM judge.

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
python main.py run \
  --service rabbitmq-experiments \
  --case    failover \
  --agent   claude-sonnet \
  --sandbox local
```

Key flags:

| Flag | Default | Description |
|---|---|---|
| `--service` | required | Service directory under `resources/` |
| `--case` | required | Case name within that service |
| `--agent` | required | Agent ID from `agents/` registry |
| `--sandbox` | `local` | `local` or `docker` |
| `--prompt-mode` | `single` | `single`, `concat_stateful`, `concat_blind` |
| `--run-dir` | auto | Root directory for run artifacts |
| `--resources-dir` | `resources/` | Override resource root |

### Run a workflow

```bash
python main.py workflow \
  --workflow resources/workflows/my-workflow.yaml \
  --agent    claude-sonnet
```

### List available cases

```bash
python main.py list-cases --service rabbitmq-experiments
```

---

## HTTP API

Start the server:

```bash
python main.py serve --port 8080
```

### `GET /health`

Returns `{"status": "ok"}` when the server is running.

### `POST /jobs`

Submit a job. Accepts JSON body with either a single case or an inline
workflow YAML:

**Single case:**

```json
{
  "service": "rabbitmq-experiments",
  "case_name": "failover",
  "agent": "claude-sonnet",
  "sandbox": "local"
}
```

**Inline workflow:**

```json
{
  "workflow_yaml": "<raw YAML string>",
  "agent": "claude-sonnet"
}
```

Response:

```json
{"run_id": "abc123", "status": "queued"}
```

### `GET /jobs`

List all jobs. Optional query param `?status=running|complete|cancelled`.

### `GET /jobs/<run_id>`

Return status and metadata for a single job.

### `DELETE /jobs/<run_id>`

Cancel a running job. Pushes a stop signal to the agent and marks the
job `cancelled`.

### `GET /jobs/<run_id>/events`

Server-Sent Events stream. Each event is a JSON object with a `type`
field (`stage_start`, `stage_end`, `workflow_end`, `error`).

```
event: stage_end
data: {"stage_id": "s1", "status": "pass", "score": 0.87}
```

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `KARMA_ORACLE_TIMEOUT_SEC` | `300` | Seconds before oracle check times out |
| `KARMA_COMMAND_TIMEOUT_SEC` | `120` | Seconds per precondition/adversary command |
| `KARMA_PRECONDITION_TIMEOUT_SEC` | `180` | Total precondition phase budget |
| `KUBECONFIG` | `~/.kube/config` | Path to kubeconfig |

---

## Project layout

```
karma/
  definitions/   case, workflow, prompt loading and validation
  environments/  Kubernetes namespace lifecycle
  metrics/       scoring metric plugins
  judge/         LLM judge client and scoring
  oracle/        automated pass/fail verification
  runtime/       stage and workflow execution loop
  transport/     kubectl proxy and agent bundle
  interfaces/    HTTP server and SSE streaming
tests/
  unit/          fast unit tests (no cluster required)
  integration/   end-to-end tests (require live cluster)
resources/       service case definitions and manifests
```
