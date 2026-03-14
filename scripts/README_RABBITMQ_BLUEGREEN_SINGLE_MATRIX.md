# RabbitMQ Blue/Green Single-Stage Sweep

This experiment runs a single-stage RabbitMQ blue/green migration workflow repeatedly
for model reliability comparison.

Files:

- `workflows/rabbitmq-blue-green-migration-single.yaml`
- `scripts/agent_single_stage_reliability.py`
- `scripts/run_codex_model_rabbitmq_bluegreen_single_matrix.sh`

## What it does

Per model:

1. Runs `orchestrator.py workflow-run` against the single-stage workflow.
2. Repeats for `--runs` attempts (default `50`).
3. Captures one log per attempt.
4. Writes run history and summary artifacts.

Matrix wrapper runs whatever models you provide.
Provide them either as positional args or `MODEL_LIST`.

## Single-model example

```bash
. .venv/bin/activate
python scripts/agent_single_stage_reliability.py \
  --workflow workflows/rabbitmq-blue-green-migration-single.yaml \
  --work-dir .benchmark/rabbitmq-bluegreen-single/gpt-5.4 \
  --runs 50 \
  --sandbox docker \
  --orchestrator-arg=--agent \
  --orchestrator-arg=cli-runner \
  --orchestrator-arg=--docker-image \
  --orchestrator-arg=bench-agent-cli-runner:latest \
  --orchestrator-arg=--agent-auth-path \
  --orchestrator-arg="$HOME/.codex/auth.json" \
  --orchestrator-arg=--agent-cmd \
  --orchestrator-arg='bash -c '"'"'set -e; export PATH=/home/agent/.npm-global/bin:$PATH; cat /opt/agent/system_prompt.txt /workspace/PROMPT.md > /tmp/codex_prompt.txt; codex --dangerously-bypass-approvals-and-sandbox exec -m gpt-5.4 -C /workspace --skip-git-repo-check "$(cat /tmp/codex_prompt.txt)"'"'"''
```

## Matrix run example

```bash
cd /Users/junhan.ouyang/personal-code/kubernetes-microservice-benchmark && \
BUILD_IMAGE=1 \
RUNS=50 \
OUT_ROOT=.benchmark/codex-model-rabbitmq-bluegreen-single-$(date -u +%Y%m%dT%H%M%SZ) \
scripts/run_codex_model_rabbitmq_bluegreen_single_matrix.sh \
  gpt-5.4 \
  gpt-5.3-codex
```

Or with env var:

```bash
MODEL_LIST='gpt-5.4,gpt-5.3-codex' \
scripts/run_codex_model_rabbitmq_bluegreen_single_matrix.sh
```

## Resume behavior

Single-model runner:

- `--resume` keeps `history.jsonl` and appends only missing attempts.
- Start attempt is `max(attempt_index)+1`.

Matrix wrapper:

- Set `RESUME_MATRIX=1` to pass `--resume` for each model.
- Keep the same `OUT_ROOT` when resuming.

Example:

```bash
RESUME_MATRIX=1 \
OUT_ROOT=.benchmark/codex-model-rabbitmq-bluegreen-single-20260311T120000Z \
MODEL_LIST='gpt-5.4,gpt-5.3-codex' \
scripts/run_codex_model_rabbitmq_bluegreen_single_matrix.sh
```

## Outputs

Per model (`<out_root>/<model-slug>/`):

- `history.jsonl`
- `summary.json`
- `aggregate_runs.csv`
- `logs/run_0001.log` ... `logs/run_0050.log`

Matrix-level (`<out_root>/`):

- `model_summary.csv`
- `aggregate_runs.csv`
