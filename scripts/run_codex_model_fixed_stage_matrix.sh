#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
BASE_WORKFLOW="${BASE_WORKFLOW:-workflows/rabbitmq-two-cycle-xy-rotation.yaml}"
TARGET_STAGE_COUNT="${TARGET_STAGE_COUNT:-50}"
MAX_RERUNS="${MAX_RERUNS:-5}"
PRECONDITION_HARD_STOP="${PRECONDITION_HARD_STOP:-1}"
RESUME_MATRIX="${RESUME_MATRIX:-0}"
OUT_ROOT="${OUT_ROOT:-.benchmark/codex-model-fixed-stage-matrix/$(date -u +%Y%m%dT%H%M%SZ)}"
AUTH_PATH="${AUTH_PATH:-$HOME/.codex/auth.json}"
DOCKER_IMAGE="${DOCKER_IMAGE:-bench-agent-cli-runner:latest}"
BUILD_IMAGE="${BUILD_IMAGE:-1}"
RUN_TIMEOUT_SEC="${RUN_TIMEOUT_SEC:-0}"
DRY_RUN_SEARCH="${DRY_RUN_SEARCH:-0}"
PROXY_SERVER="${PROXY_SERVER:-}"

case "$(printf '%s' "$PRECONDITION_HARD_STOP" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|y)
    PRECONDITION_HARD_STOP=1
    ;;
  0|false|no|n)
    PRECONDITION_HARD_STOP=0
    ;;
  *)
    echo "invalid PRECONDITION_HARD_STOP: $PRECONDITION_HARD_STOP (expected 0/1/true/false)" >&2
    exit 1
    ;;
esac

case "$(printf '%s' "$RESUME_MATRIX" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|y)
    RESUME_MATRIX=1
    ;;
  0|false|no|n)
    RESUME_MATRIX=0
    ;;
  *)
    echo "invalid RESUME_MATRIX: $RESUME_MATRIX (expected 0/1/true/false)" >&2
    exit 1
    ;;
esac

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "python interpreter not found or not executable: $PYTHON_BIN" >&2
  echo "tip: set PYTHON_BIN=.venv/bin/python (or activate .venv first)." >&2
  exit 1
fi

if [[ ! -f "$BASE_WORKFLOW" ]]; then
  echo "base workflow not found: $BASE_WORKFLOW" >&2
  exit 1
fi

if [[ ! -f "$AUTH_PATH" ]]; then
  echo "auth file not found: $AUTH_PATH" >&2
  exit 1
fi

if [[ "$BUILD_IMAGE" == "1" ]]; then
  echo "[fixed-50-matrix] building docker image: $DOCKER_IMAGE"
  docker build -t "$DOCKER_IMAGE" -f agent_tests/cli-runner/Dockerfile .
fi

mkdir -p "$OUT_ROOT"
RUNS_CSV="$OUT_ROOT/aggregate_runs.csv"
MODELS_CSV="$OUT_ROOT/model_summary.csv"

cat > "$RUNS_CSV" <<'CSV'
model,attempt_index,stage_count,passed,status,classification,retryable,hard_stop,failure_stage_index,terminal_reason,cleanup_status,failed_stage_id,failed_stage_status,failed_stage_reason,failed_stage_source,returncode,log_path,workflow_path
CSV

cat > "$MODELS_CSV" <<'CSV'
model,status,complete_50,attempts_used,max_reruns,target_stage_count,average_failed_stage_index,stop_reason,matrix_pause_required,pause_classification,history_path
CSV

if [[ "$#" -gt 0 ]]; then
  MODELS=("$@")
else
  MODELS=(
    "gpt-5.1-codex-mini"
    "gpt-5.2"
    "gpt-5.2-codex"
    "gpt-5.1-codex-max"
    "gpt-5.3-codex"
  )
fi

for MODEL in "${MODELS[@]}"; do
  SLUG="$(printf '%s' "$MODEL" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9._-' '-' | sed 's/^-*//; s/-*$//')"
  if [[ -z "$SLUG" ]]; then
    SLUG="model"
  fi
  WORK_DIR="$OUT_ROOT/$SLUG"
  mkdir -p "$WORK_DIR"

  AGENT_CMD="bash -c 'set -e; export PATH=/home/agent/.npm-global/bin:\$PATH; command -v codex >/dev/null 2>&1 || { echo \"codex not found PATH=\$PATH\" >&2; exit 127; }; cat /opt/agent/system_prompt.txt /workspace/PROMPT.md > /tmp/codex_prompt.txt; codex --dangerously-bypass-approvals-and-sandbox exec -m ${MODEL} -C /workspace --skip-git-repo-check \"\$(cat /tmp/codex_prompt.txt)\"'"

  CMD=(
    "$PYTHON_BIN" scripts/agent_fixed_stage_reliability.py
    --base-workflow "$BASE_WORKFLOW"
    --work-dir "$WORK_DIR"
    --target-stage-count "$TARGET_STAGE_COUNT"
    --max-reruns "$MAX_RERUNS"
    --run-timeout-sec "$RUN_TIMEOUT_SEC"
    --sandbox docker
    --orchestrator-arg=--agent
    --orchestrator-arg=cli-runner
    --orchestrator-arg=--docker-image
    --orchestrator-arg="$DOCKER_IMAGE"
    --orchestrator-arg=--agent-auth-path
    --orchestrator-arg="$AUTH_PATH"
    --orchestrator-arg=--agent-cmd
    --orchestrator-arg="$AGENT_CMD"
  )
  if [[ "$PRECONDITION_HARD_STOP" == "0" ]]; then
    CMD+=(--no-precondition-hard-stop)
  fi
  if [[ "$RESUME_MATRIX" == "1" ]]; then
    CMD+=(--resume)
  fi
  if [[ -n "$PROXY_SERVER" ]]; then
    CMD+=(--orchestrator-arg=--proxy-server --orchestrator-arg="$PROXY_SERVER")
  fi
  if [[ "$DRY_RUN_SEARCH" == "1" ]]; then
    CMD+=(--dry-run)
  fi

  echo "[fixed-50-matrix] model=$MODEL work_dir=$WORK_DIR"
  set +e
  "${CMD[@]}" | tee "$WORK_DIR/search.stdout.log"
  SEARCH_EXIT=${PIPESTATUS[0]}
  set -e

  SUMMARY_JSON="$WORK_DIR/summary.json"
  "$PYTHON_BIN" - "$MODEL" "$SUMMARY_JSON" "$RUNS_CSV" "$MODELS_CSV" <<'PY'
import csv
import json
import sys
from pathlib import Path

model = sys.argv[1]
summary_path = Path(sys.argv[2])
runs_csv = Path(sys.argv[3])
models_csv = Path(sys.argv[4])

if not summary_path.exists():
    raise SystemExit(f"summary file missing: {summary_path}")

summary = json.loads(summary_path.read_text(encoding="utf-8"))
runs = summary.get("runs") if isinstance(summary.get("runs"), list) else []

with runs_csv.open("a", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    for run in runs:
        if not isinstance(run, dict):
            continue
        writer.writerow(
            [
                model,
                run.get("attempt_index"),
                run.get("stage_count"),
                run.get("passed"),
                run.get("status"),
                run.get("classification"),
                run.get("retryable"),
                run.get("hard_stop"),
                run.get("failure_stage_index"),
                run.get("terminal_reason"),
                run.get("cleanup_status"),
                run.get("failed_stage_id"),
                run.get("failed_stage_status"),
                run.get("failed_stage_reason"),
                run.get("failed_stage_source"),
                run.get("returncode"),
                run.get("log_path"),
                run.get("workflow_path"),
            ]
        )

with models_csv.open("a", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(
        [
            model,
            summary.get("status"),
            summary.get("complete_50"),
            summary.get("attempts_used"),
            summary.get("max_reruns"),
            summary.get("target_stage_count"),
            summary.get("average_failed_stage_index"),
            summary.get("stop_reason"),
            summary.get("matrix_pause_required"),
            summary.get("pause_classification"),
            summary.get("history_path"),
        ]
    )
PY

  if [[ "$SEARCH_EXIT" -ne 0 ]]; then
    echo "[fixed-50-matrix] paused on model=$MODEL exit=$SEARCH_EXIT summary=$SUMMARY_JSON" >&2
    exit "$SEARCH_EXIT"
  fi
done

echo "[fixed-50-matrix] done"
echo "[fixed-50-matrix] model summary: $MODELS_CSV"
echo "[fixed-50-matrix] run-level detail: $RUNS_CSV"
