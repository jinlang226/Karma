# Fixed-Stage Model Sweep (50-stage)

This README documents:

- `/Users/jimmyouyang/code/kubernetes-microservice-benchmark/scripts/agent_fixed_stage_reliability.py`
- `/Users/jimmyouyang/code/kubernetes-microservice-benchmark/scripts/run_codex_model_fixed_stage_matrix.sh`

These scripts run a fixed-stage workflow (default: 50 stages) across models, with bounded reruns and explicit pause conditions.

## What this sweep does

For each model:

1. Generate a workflow with exactly `target_stage_count` stages (default `50`) by repeating/truncating the base workflow stages.
2. Run orchestrator workflow once.
3. If the run passes, mark `complete_50` and stop reruns for this model.
4. If the run is retryable (`agent_give_up`, `oracle_fail`, or other non-hard stage failure), rerun up to `max_reruns` (default `5`).
5. If all reruns fail, report `failed_after_max_reruns` and compute `average_failed_stage_index`.
6. If a hard-stop condition occurs, pause immediately.

If `--resume` is enabled (or `RESUME_MATRIX=1` in matrix wrapper), existing `<work_dir>/history.jsonl` is loaded and only remaining attempts are executed.

## Hard-stop (pause) conditions

The runner sets `matrix_pause_required=true` and exits non-zero when classification is:

- `precondition_failure` (only when `--precondition-hard-stop` / `PRECONDITION_HARD_STOP=1`)
- `agent_runtime_error` (agent exited non-zero)
- `infra_abort` (parse/process/timeout/cleanup infrastructure errors)

In matrix mode, the shell wrapper stops the full model sweep at the first hard-stop model.

## Retryable conditions

Retryable failures (counted toward `max_reruns`) include:

- `agent_give_up` (agent exited 0 without completing stage/workflow)
- `oracle_fail`
- `retryable_failure` (other non-hard stage failure)
- `precondition_failure` when precondition hard-stop is disabled (`--no-precondition-hard-stop` or `PRECONDITION_HARD_STOP=0`)

## Relative `case_path` handling

If the base workflow uses relative `case_path`, the Python runner normalizes it to an absolute path in generated workflows.  
This prevents resolution breakage when generated YAML files live in a different directory.

## Script 1: single-model fixed-stage runner

`agent_fixed_stage_reliability.py` runs one model/config and writes per-attempt artifacts.

### Example

```bash
cd /Users/jimmyouyang/code/kubernetes-microservice-benchmark

.venv/bin/python scripts/agent_fixed_stage_reliability.py \
  --base-workflow workflows/rabbitmq-two-cycle-xy-rotation.yaml \
  --work-dir .benchmark/fixed50-single/gpt-5.2-codex \
  --target-stage-count 50 \
  --max-reruns 5 \
  --resume \
  --no-precondition-hard-stop \
  --sandbox docker \
  --orchestrator-arg=--agent \
  --orchestrator-arg=cli-runner \
  --orchestrator-arg=--docker-image \
  --orchestrator-arg=bench-agent-cli-runner:latest \
  --orchestrator-arg=--agent-auth-path \
  --orchestrator-arg=$HOME/.codex/auth.json \
  --orchestrator-arg=--agent-cmd \
  --orchestrator-arg='bash -c '"'"'set -e; export PATH=/home/agent/.npm-global/bin:$PATH; cat /opt/agent/system_prompt.txt /workspace/PROMPT.md > /tmp/codex_prompt.txt; codex --dangerously-bypass-approvals-and-sandbox exec -m gpt-5.2-codex -C /workspace --skip-git-repo-check "$(cat /tmp/codex_prompt.txt)"'"'"''
```

### Key outputs

- `<work_dir>/summary.json`: canonical per-model summary
- `<work_dir>/history.jsonl`: one JSON record per attempt
- `<work_dir>/logs/run_0001.log`, etc.
- `<work_dir>/generated_workflows/workflow_fixed50_attempt1.yaml`, etc.

## Script 2: multi-model matrix wrapper

`run_codex_model_fixed_stage_matrix.sh` loops models and aggregates outputs.

### Example

```bash
cd /Users/jimmyouyang/code/kubernetes-microservice-benchmark && \
BUILD_IMAGE=1 \
OUT_ROOT=.benchmark/codex-model-fixed-stage-matrix-$(date -u +%Y%m%dT%H%M%SZ) \
TARGET_STAGE_COUNT=50 \
MAX_RERUNS=5 \
RESUME_MATRIX=1 \
PRECONDITION_HARD_STOP=0 \
RUN_TIMEOUT_SEC=7200 \
scripts/run_codex_model_fixed_stage_matrix.sh \
  gpt-5.1-codex-mini \
  gpt-5.2 \
  gpt-5.2-codex \
  gpt-5.1-codex-max \
  gpt-5.3-codex
```

### Matrix outputs

- `<out_root>/model_summary.csv`: one row per model
- `<out_root>/aggregate_runs.csv`: one row per attempt
- `<out_root>/<model-slug>/summary.json`
- `<out_root>/<model-slug>/history.jsonl`

## Precondition failure policy

- Default behavior: precondition/setup failures are hard-stop and pause matrix.
- To make precondition failures retryable instead of immediate pause:
  - Python runner: pass `--no-precondition-hard-stop`
  - Matrix wrapper: set `PRECONDITION_HARD_STOP=0`

## Resume policy

- Python runner: pass `--resume` to reuse `<work_dir>/history.jsonl`.
- Matrix wrapper: set `RESUME_MATRIX=1` to pass `--resume` to each model run.
- Resume behavior:
  - If attempts already reached `max_reruns`, no new run is started.
  - If prior history already contains `complete_50`, no new run is started.
  - If prior history contains a hard-stop classification under current policy, no new run is started.
  - Otherwise runner starts from `next_attempt = attempts_used + 1`.

## How to parse results

### 1) Determine model-level outcome quickly

```bash
jq -r '.status, .complete_50, .attempts_used, .average_failed_stage_index, .stop_reason, .matrix_pause_required, .pause_classification' \
  /path/to/work_dir/summary.json
```

Interpretation:

- `status=complete_50`: model reached end of fixed-stage workflow; no reruns needed.
- `status=failed_after_max_reruns`: model failed all retries; use `average_failed_stage_index`.
- `status=matrix_pause_required`: hard-stop condition; investigate immediately.

### 2) Parse attempt-level failure stage

```bash
jq -r '.runs[] | [.attempt_index, .classification, .failure_stage_index, .failed_stage_id, .terminal_reason, .hard_stop] | @tsv' \
  /path/to/work_dir/summary.json
```

Notes:

- `failure_stage_index` is the stage index used for averaging.
- `failed_stage_id` is the concrete stage identifier from workflow state/results.
- `hard_stop=true` means matrix should pause.

### 3) Read matrix CSVs

`model_summary.csv` columns:

- `model`
- `status`
- `complete_50`
- `attempts_used`
- `max_reruns`
- `target_stage_count`
- `average_failed_stage_index`
- `stop_reason`
- `matrix_pause_required`
- `pause_classification`
- `history_path`

`aggregate_runs.csv` columns:

- `model`
- `attempt_index`
- `stage_count`
- `passed`
- `status`
- `classification`
- `retryable`
- `hard_stop`
- `failure_stage_index`
- `terminal_reason`
- `cleanup_status`
- `failed_stage_id`
- `failed_stage_status`
- `failed_stage_reason`
- `failed_stage_source`
- `returncode`
- `log_path`
- `workflow_path`

### 4) Ranking heuristic (simple)

If no model hard-stopped:

1. Prefer `complete_50=true`
2. Then higher `average_failed_stage_index` (for failed models)
3. Then lower `attempts_used`

If any model has `matrix_pause_required=true`, treat result as invalid for capability ranking until infra/setup issue is resolved.

## Exit codes

`agent_fixed_stage_reliability.py`:

- `0`: complete_50 or failed_after_max_reruns (non-hard result)
- `2`: hard-stop (`matrix_pause_required=true`)

`run_codex_model_fixed_stage_matrix.sh`:

- `0`: all models processed without hard-stop
- non-zero: paused on a hard-stop model

## Validation done

Validated in this repo:

1. Syntax checks:
   - `python -m py_compile scripts/agent_fixed_stage_reliability.py`
   - `bash -n scripts/run_codex_model_fixed_stage_matrix.sh`
2. Dry-run matrix:
   - generated summaries and CSVs successfully
3. Real smoke (local fixture pass path):
   - complete_50 in one attempt
4. Real smoke (retry path):
   - retried to max and computed `average_failed_stage_index`
5. Hard-stop behavior:
   - correctly pauses on hard-stop classifications
