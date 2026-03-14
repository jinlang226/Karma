#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
WORKFLOW="${WORKFLOW:-workflows/rabbitmq-blue-green-migration-single.yaml}"
RUNS="${RUNS:-50}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-1}"
STAGE_FAILURE_MODE="${STAGE_FAILURE_MODE:-terminate}"
FINAL_SWEEP_MODE="${FINAL_SWEEP_MODE:-off}"
RESUME_MATRIX="${RESUME_MATRIX:-0}"
OUT_ROOT="${OUT_ROOT:-.benchmark/codex-model-rabbitmq-bluegreen-single-matrix/$(date -u +%Y%m%dT%H%M%SZ)}"
AUTH_PATH="${AUTH_PATH:-$HOME/.codex/auth.json}"
DOCKER_IMAGE="${DOCKER_IMAGE:-bench-agent-cli-runner:latest}"
BUILD_IMAGE="${BUILD_IMAGE:-1}"
RUN_TIMEOUT_SEC="${RUN_TIMEOUT_SEC:-0}"
DRY_RUN_SWEEP="${DRY_RUN_SWEEP:-0}"
PROXY_SERVER="${PROXY_SERVER:-}"
MODEL_LIST="${MODEL_LIST:-}"

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

if [[ ! -f "$WORKFLOW" ]]; then
  echo "workflow not found: $WORKFLOW" >&2
  exit 1
fi

if [[ ! -f "$AUTH_PATH" ]]; then
  echo "auth file not found: $AUTH_PATH" >&2
  exit 1
fi

if [[ "$BUILD_IMAGE" == "1" ]]; then
  echo "[single-stage-matrix] building docker image: $DOCKER_IMAGE"
  docker build -t "$DOCKER_IMAGE" -f agent_tests/cli-runner/Dockerfile .
fi

mkdir -p "$OUT_ROOT"
RUNS_CSV="$OUT_ROOT/aggregate_runs.csv"
MODELS_CSV="$OUT_ROOT/model_summary.csv"

cat > "$RUNS_CSV" <<'CSV'
model,attempt_index,stage_count,passed,status,classification,terminal_reason,cleanup_status,failure_stage_index,failed_stage_id,failed_stage_status,failed_stage_reason,failed_stage_source,active_stage_index,active_stage_id,returncode,parse_error,log_path,workflow_path
CSV

cat > "$MODELS_CSV" <<'CSV'
model,runs_target,total_runs,pass_count,fail_count,pass_rate,classification_counts,history_path,summary_path
CSV

MODELS=()
if [[ "$#" -gt 0 ]]; then
  MODELS=("$@")
elif [[ -n "$MODEL_LIST" ]]; then
  NORMALIZED_MODEL_LIST="$(printf '%s' "$MODEL_LIST" | tr ',' '\n')"
  while IFS= read -r RAW_MODEL; do
    MODEL="$(printf '%s' "$RAW_MODEL" | xargs)"
    if [[ -n "$MODEL" ]]; then
      MODELS+=("$MODEL")
    fi
  done <<< "$NORMALIZED_MODEL_LIST"
fi

if [[ "${#MODELS[@]}" -eq 0 ]]; then
  echo "no models specified." >&2
  echo "pass model ids as positional args, or set MODEL_LIST='gpt-5.4,gpt-5.3-codex'" >&2
  exit 1
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
    "$PYTHON_BIN" scripts/agent_single_stage_reliability.py
    --workflow "$WORKFLOW"
    --work-dir "$WORK_DIR"
    --runs "$RUNS"
    --max-attempts "$MAX_ATTEMPTS"
    --stage-failure-mode "$STAGE_FAILURE_MODE"
    --final-sweep-mode "$FINAL_SWEEP_MODE"
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
  if [[ "$RESUME_MATRIX" == "1" ]]; then
    CMD+=(--resume)
  fi
  if [[ -n "$PROXY_SERVER" ]]; then
    CMD+=(--orchestrator-arg=--proxy-server --orchestrator-arg="$PROXY_SERVER")
  fi
  if [[ "$DRY_RUN_SWEEP" == "1" ]]; then
    CMD+=(--dry-run)
  fi

  echo "[single-stage-matrix] model=$MODEL work_dir=$WORK_DIR"
  set +e
  "${CMD[@]}" | tee "$WORK_DIR/sweep.stdout.log"
  SWEEP_EXIT=${PIPESTATUS[0]}
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
stats = summary.get("summary") if isinstance(summary.get("summary"), dict) else {}

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
                run.get("terminal_reason"),
                run.get("cleanup_status"),
                run.get("failure_stage_index"),
                run.get("failed_stage_id"),
                run.get("failed_stage_status"),
                run.get("failed_stage_reason"),
                run.get("failed_stage_source"),
                run.get("active_stage_index"),
                run.get("active_stage_id"),
                run.get("returncode"),
                run.get("parse_error"),
                run.get("log_path"),
                run.get("workflow_path"),
            ]
        )

with models_csv.open("a", newline="", encoding="utf-8") as handle:
    writer = csv.writer(handle)
    writer.writerow(
        [
            model,
            summary.get("runs_target"),
            stats.get("total_runs"),
            stats.get("pass_count"),
            stats.get("fail_count"),
            stats.get("pass_rate"),
            json.dumps(stats.get("classification_counts") or {}, sort_keys=True),
            summary.get("history_path"),
            str(summary_path),
        ]
    )
PY

  if [[ "$SWEEP_EXIT" -ne 0 ]]; then
    echo "[single-stage-matrix] model=$MODEL failed with exit=$SWEEP_EXIT summary=$SUMMARY_JSON" >&2
    exit "$SWEEP_EXIT"
  fi
done

echo "[single-stage-matrix] done"
echo "[single-stage-matrix] model summary: $MODELS_CSV"
echo "[single-stage-matrix] run-level detail: $RUNS_CSV"
