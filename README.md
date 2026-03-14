# Kubernetes Agent Benchmark

Manual benchmark runner and test case corpus for Kubernetes microservice e2e tasks.

## Quick start

```bash
pip install -r requirements.txt
python3 main.py
```

Open http://localhost:8080 in your browser.

## Full flow (recommended)

1) (Optional) Start proxy + kubeconfig setup:

```bash
source ./scripts/setup-proxy.sh
```

2) Start the runner UI:

```bash
pip install -r requirements.txt
python3 main.py
```

3) In the UI, select a case, click Start, then Submit after you apply fixes.
   - You can also click **Generate CLI** at service/case scope to preview and copy
     an equivalent `python3 orchestrator.py ...` command.
   - The command builder has a basic section plus an advanced panel for full flag control.

4) Logs and metrics are written under `runs/<run_id>/`:
   - `preoperation.log`, `verification_*.log`, `cleanup.log`
   - `external_metrics.json`
   - `action_trace.jsonl` (only when proxy control is enabled; includes kubectl commands and proxy connection events)

## Test runners

Unit tests:

```bash
python3 tests/run_unit.py
```

Integration tests:

```bash
python3 tests/run_integration.py
```

Notes:
- Integration fixture cases live under `tests/fixtures/resources/smoke-orchestrator/`.
- Synthetic smoke cases are not part of the production `resources/` corpus.

## Optional: enhanced metrics proxy

If you run a local API proxy, you can route kubectl traffic through it to capture
read/write traces. This requires a proxy kubeconfig that points kubectl to the
proxy while preserving your cluster credentials.

### Step 1: Use the helper script (recommended)

```bash
source ./scripts/setup-proxy.sh
```

This starts the proxy if needed, generates `./.benchmark/kubeconfig-proxy`,
and exports `KUBECONFIG` and `BENCHMARK_PROXY_CONTROL_URL` for the current shell.
It also installs a local `kubectl` wrapper under `./.benchmark/bin` that logs
kubectl commands to the per-run trace file.

### Manual setup

If you prefer to do this manually, follow the steps below.

### Step 1: Find your real API server

```bash
KUBECONFIG=~/.kube/config kubectl config view --minify \
  -o jsonpath='{.clusters[0].cluster.server}'
```

Example output:
```
https://127.0.0.1:53921
```

### Step 2: Start the proxy

Use the API server host:port from Step 1 (strip `https://`):

```bash
python3 proxy.py --listen 127.0.0.1:8081 --upstream 127.0.0.1:53921 \
  --control-listen 127.0.0.1:8082
```

### Step 3: Create a proxy kubeconfig

```bash
mkdir -p .benchmark
cp ~/.kube/config .benchmark/kubeconfig-proxy
```

Edit `./.benchmark/kubeconfig-proxy` and set the cluster server to:

```
https://127.0.0.1:8081
```

All other credentials should stay the same as your original kubeconfig.

### Step 4: Point kubectl to the proxy

```bash
source ./benchmark-env.sh
```

Test that it works:

```bash
kubectl get ns
```

### Step 5: Start the runner

```bash
export BENCHMARK_PROXY_CONTROL_URL=http://127.0.0.1:8082
python3 main.py
```

Each run will create `runs/<run_id>/action_trace.jsonl` and the proxy will switch
to that path automatically.

### Simple TCP proxy (no TLS termination)

If you just need a lightweight connection log without per-run separation, you can run:

```bash
python3 proxy.py --listen 127.0.0.1:8081 --upstream 127.0.0.1:53921 \
  --log-file runs/proxy-trace.jsonl
```

This proxy only forwards bytes and logs connection metadata (bytes and duration).
It does not terminate TLS or log request paths.

## Headless orchestrator (agent eval)

The orchestrator runs cases without the UI and prepares an isolated agent bundle
under `runs/<run_id>/agent_bundle`. It prints stage updates and streams kubectl
commands to stdout for live debugging.

### Execution architecture

- `orchestrator.py` is a thin entrypoint only.
  - Stable contract: CLI behavior (`python3 orchestrator.py ...`) and `orchestrator.main`.
  - `__all__` exports only `main` (no compatibility runtime internals).
- `app/orchestrator_core/runtime_glue.py` is the composition root for CLI wiring and dependency injection.
  - Focused adapters are split into:
    - `app/orchestrator_core/glue_runtime.py`
    - `app/orchestrator_core/glue_judge.py`
    - `app/orchestrator_core/glue_workflow.py`
- `app/orchestrator_core/workflow_engine.py` is the canonical execution loop.
  - `workflow-run` calls it directly through `app/orchestrator_core/workflow_run.py`.
  - `run`/`batch` use a synthetic single-stage workflow plan, so they execute through the same engine path.
- `app/orchestrator_core/case_runner.py` handles single-case and batch dispatch into the shared workflow engine path.
- Judge routing uses normalized `run_dir` values so post-run and post-batch judging resolve unified artifacts consistently.

### Agent configuration (recommended)

Each agent supports a `config.env` file that the orchestrator auto-loads. Create
`agent_tests/react/config.env` (or the matching agent folder) with:

```
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=sk-...
# LLM_BASE_URL=https://api.openai.com/v1
# REACT_STEP_DELAY_SEC=1
```

You can also pass `--llm-env-file` to override the auto-loaded config.

### Single case (manual agent)

```bash
python3 orchestrator.py run --service nginx-ingress --case renew_tls_secret
```

In another terminal, open the bundle and use its env:

```bash
RUN_ID=$(ls -t runs | head -1)
cd "runs/$RUN_ID/agent_bundle"
source ./env.sh
# run your agent or manual kubectl commands here
touch submit.signal
```

### Single case (auto agent command)

```bash
python3 orchestrator.py run --service nginx-ingress --case renew_tls_secret \
  --agent-cmd "bash -lc 'source ./env.sh; <agent command>'"
```

### Single case (auto agent image)

```bash
python3 orchestrator.py run \
  --sandbox docker \
  --agent react \
  --agent-build \
  --service nginx-ingress \
  --case class_only_upgrade
```

### Orchestrator flags (reference)

Common selectors:
- `--case-id` Base64 case id (overrides service/case).
- `--service` Service name (e.g., `nginx-ingress`).
- `--case` Case name (e.g., `class_only_upgrade`).
- `--all` Run all cases (batch mode only).

Agent configuration:
- `--agent` Agent name (auto-discovered from `agent_tests/*/Dockerfile`, e.g. `react`, `cli-runner`).
- `--agent-build` Build the agent image (Docker sandbox only).
- `--agent-tag` Override the built image tag.
- `--agent-cleanup` Remove the built image after the run.
- `--agent-cmd` Command to launch the agent inside the bundle/container.
- `--llm-env-file` Path to an explicit LLM env file (overrides auto-loaded config).

Sandbox + network:
- `--sandbox` `local` or `docker`.
- `--docker-image` Image to run in Docker sandbox (optional if using `--agent` + `--agent-build`).

Kubernetes + proxy:
- `--source-kubeconfig` Source kubeconfig path (defaults to current context).
- `--proxy-server` Proxy listen host:port override (default is used automatically when omitted).
- `--real-kubectl` Path to real kubectl (local sandbox only).

Submission + timeouts:
- `--submit-timeout` Max seconds to wait for submit (default 1200).
- `--setup-timeout` Setup/precondition timeout floor in `auto` mode, hard cap in `fixed` mode (default 600).
- `--setup-timeout-mode` `auto` (default) or `fixed`.
  - `auto`: orchestrator waits for `max(--setup-timeout, setup_timeout_auto_sec)` where `setup_timeout_auto_sec`
    is computed per run from the case preOperationCommands (sum of per-step timeouts + sleeps, plus a small slack).
  - `fixed`: orchestrator waits for exactly `--setup-timeout`.
- `--verify-timeout` Verification timeout (default 1200).
- `--cleanup-timeout` Cleanup timeout (default 600).
- `--max-attempts` Override max submit attempts (global cap; applies to single-case and workflow stages).

Trajectory judge (LLM-as-Judge, optional):
- `--judge-mode` `off|post-run|post-batch` (default `off`).
- `--judge-model` Judge model name.
- `--judge-base-url` Judge API base URL (OpenAI-compatible, works with OpenRouter).
- `--judge-api-key` Judge API key (or use env `JUDGE_API_KEY` / `LLM_API_KEY` / `OPENAI_API_KEY`).
- `--judge-timeout` Judge API timeout seconds (default 120).
- `--judge-max-retries` Judge API retry count (default 2).
- `--judge-prompt-version` Prompt version tag written into artifacts (default `v1`).
- `--judge-include-outcome` Include final pass/fail in judge input (default off to reduce outcome bias).
- `--judge-fail-closed` Fail orchestrator if judge call fails (default behavior is fail-open).

Judge artifacts (when enabled):
- Per run: `runs/<run_id>/judge/input_v1.json`, `result_v1.json`, `raw_response.json`, `summary.md`
- Per batch: `runs/batch_<ts>/judge_index.json`, `judge_summary.json`, `judge_leaderboard.csv`

Standalone judge runner:
- `python3 scripts/judge.py run --run-dir runs/<run_dir>`
- `python3 scripts/judge.py batch --batch-dir runs/batch_<ts>`
- It auto-loads judge config from `judge.env` (or `config/judge.env`) by default.
- Override config source with `--judge-env-file <path>`.
- Judge client uses the OpenAI Python SDK (`openai`) against an OpenAI-compatible endpoint (for example OpenRouter).

Agent token usage (optional, backward compatible):
- `cli-runner` now emits raw token usage to `runs/<run_id>/agent_usage_raw.json` (best effort).
- Orchestrator normalizes and writes:
  - `runs/<run_id>/agent_usage.json`
  - `external_metrics.json` key: `agent_token_usage`
  - `meta.json` fields: `token_usage_*` and `agent_usage_path`
- Missing or unavailable token data does not fail a run.

Rubric files:
- Case rubric (optional): `resources/<service>/<case>/judge.yaml`

Minimal case rubric example:

```yaml
rubric_id: rabbitmq.manual_monitoring.v1
rubric_version: "1"
objective_weights:
  process_quality: 0.7
  efficiency: 0.3
questions:
  - id: diagnosis_speed
    track: process_quality
    weight: 0.4
    prompt: Did the agent quickly identify the root cause with evidence?
  - id: resource_efficiency
    track: efficiency
    weight: 1.0
    prompt: Was the run efficient in time/commands/tokens?
milestones:
  - Confirm failure mode with direct checks.
  - Apply a durable fix and re-verify.
anti_patterns:
  - Blind retries without new evidence.
```

### Per-step command timeouts (test.yaml)

Command lists (e.g. `preOperationCommands`, `verificationCommands`, `cleanUpCommands`) support an optional
`timeout_sec` field per step. This is a runner-side wall-clock timeout for the command execution and is also
used to compute the run's `setup_timeout_auto_sec` budget.

Example:

```yaml
preOperationCommands:
  - command: ["kubectl", "-n", "rabbitmq", "apply", "-f", "resource/statefulset.yaml"]
    sleep: 1
    timeout_sec: 120
  - command: ["/bin/sh", "-c", "kubectl wait --for=condition=ready pod -l app=rabbitmq --timeout=600s"]
    sleep: 0
```

### Setup phase flow and checks

Setup now runs in this order:

1. `preOperationCommands` (baseline apply)
2. `setup_self_check.precondition_check` (optional baseline check loop)
3. decoy apply (when enabled)
4. setup marked `ready` and solve timer starts

`run_status` includes:
- `setup_phase`
- `setup_warnings`
- `setup_checks_path` (JSON summary written under run dir)

Service-level precondition check example:

```yaml
setup_self_check:
  precondition_check:
    mode: required        # required | warn | off
    budget_sec: 180
    poll_sec: 5
    consecutive_passes: 2
    commands:
      - command: ["python3", "resources/rabbitmq-experiments/common/setup_precondition_check.py", "--namespace", "rabbitmq"]
        timeout_sec: 30
        sleep: 0
```

Notes:
- If `setup_self_check.precondition_check` is missing, setup continues with a warning.

### Verification hooks (oracle setup/teardown)

You can run commands immediately before and after `verificationCommands` using
`verification_hooks` (or camelCase `verificationHooks`):

```yaml
verification_hooks:
  before_commands:
    - command: ["kubectl", "-n", "rabbitmq", "apply", "-f", "resources/rabbitmq-experiments/common/oracle-client.yaml"]
      sleep: 0
  after_commands:
    - command: ["kubectl", "-n", "rabbitmq", "delete", "-f", "resources/rabbitmq-experiments/common/oracle-client.yaml", "--ignore-not-found=true"]
      sleep: 0
  after_failure_mode: warn   # warn | fail
```

Behavior:
- `before_commands` run before oracle commands.
- `after_commands` always run in a `finally` block, even when verification fails.
- `after_failure_mode=warn` keeps verification result unchanged and records a warning.
- `after_failure_mode=fail` marks the verification attempt failed if cleanup hook fails.

### Batch mode

```bash
python3 orchestrator.py batch --service nginx-ingress \
  --results-json runs/batch_results.json
```

### Docker sandbox (optional)

```bash
python3 orchestrator.py run --service nginx-ingress --case renew_tls_secret \
  --sandbox docker --docker-image <agent-image>
```

If your image does not define an ENTRYPOINT, pass a command explicitly:
```bash
python3 orchestrator.py run --service nginx-ingress --case renew_tls_secret \
  --sandbox docker --docker-image <agent-image> --agent-cmd "python /app/run_agent.py"
```
