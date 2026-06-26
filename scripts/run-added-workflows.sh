#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  PYTHON="${PYTHON:-python3}"
fi

AGENT="${KARMA_RUN_AGENT:-codex}"
SANDBOX="${KARMA_RUN_SANDBOX:-local}"
RUNS_ROOT="${KARMA_RUNS_ROOT:-runs}"
MAX_ATTEMPTS="${KARMA_MAX_ATTEMPTS:-1}"
STAGE_FAILURE_MODE="${KARMA_STAGE_FAILURE_MODE:-terminate}"
FINAL_SWEEP_MODE="${KARMA_FINAL_SWEEP_MODE:-off}"
SETUP_TIMEOUT="${KARMA_SETUP_TIMEOUT:-}"
VERIFY_TIMEOUT="${KARMA_VERIFY_TIMEOUT:-}"
CLEANUP_TIMEOUT="${KARMA_CLEANUP_TIMEOUT:-}"
STOP_ON_FAILURE="${KARMA_STOP_ON_FAILURE:-0}"
RESET_NAMESPACES="${KARMA_RESET_NAMESPACES:-1}"
RESET_NAMESPACE_LIST="${KARMA_RESET_NAMESPACE_LIST:-cockroachdb elasticsearch mongodb rabbitmq ray spark-pi spark-skew spark-etl spark-team-a spark-team-b spark-history demo ingress-nginx ingress-nginx-2 monitoring otel}"
RESET_WAIT_TIMEOUT="${KARMA_RESET_WAIT_TIMEOUT:-240s}"
KUBECTL_REQUEST_TIMEOUT="${KARMA_KUBECTL_REQUEST_TIMEOUT:-5s}"
CLUSTER_CHECK_ATTEMPTS="${KARMA_CLUSTER_CHECK_ATTEMPTS:-2}"
CLUSTER_CHECK_SLEEP="${KARMA_CLUSTER_CHECK_SLEEP:-2}"

STAMP="$(date +%Y%m%d_%H%M%S)"
BATCH_DIR="${1:-$RUNS_ROOT/new-workflows-${AGENT}-${STAMP}}"
LOG_FILE="$BATCH_DIR/batch.log"
PROGRESS_FILE="$BATCH_DIR/batch-progress.jsonl"

mkdir -p "$BATCH_DIR"
touch "$LOG_FILE"
touch "$PROGRESS_FILE"

log() {
  echo "$@" | tee -a "$LOG_FILE"
}

if [ "$AGENT" = "codex" ] && ! command -v codex >/dev/null 2>&1; then
  CODEX_BIN="$(find "$HOME/.vscode/extensions" -path '*/openai.chatgpt-*/bin/*/codex' -type f 2>/dev/null | sort -r | head -n 1 || true)"
  if [ -n "$CODEX_BIN" ]; then
    export PATH="$(dirname "$CODEX_BIN"):$PATH"
  fi
fi

if [ "$AGENT" = "codex" ] && ! command -v codex >/dev/null 2>&1; then
  echo "error: AGENT=codex but 'codex' is not on PATH" >&2
  echo "Set PATH to include your Codex binary, or run with KARMA_RUN_AGENT=api/claude_code/etc." >&2
  exit 1
fi

check_cluster() {
  if [ "${KARMA_SKIP_CLUSTER_CHECK:-0}" = "1" ]; then
    log "Skipping Kubernetes cluster preflight because KARMA_SKIP_CLUSTER_CHECK=1."
    return 0
  fi

  log "Checking Kubernetes cluster..."
  local attempt
  local err_file="$BATCH_DIR/cluster-check.err"
  for attempt in $(seq 1 "$CLUSTER_CHECK_ATTEMPTS"); do
    if kubectl --request-timeout="$KUBECTL_REQUEST_TIMEOUT" get nodes > /dev/null 2> "$err_file"; then
      rm -f "$err_file"
      log "Kubernetes cluster reachable."
      return 0
    fi

    log "Cluster check failed (attempt $attempt/$CLUSTER_CHECK_ATTEMPTS): $(tr '\n' ' ' < "$err_file")"
    if [ "$attempt" -lt "$CLUSTER_CHECK_ATTEMPTS" ]; then
      sleep "$CLUSTER_CHECK_SLEEP"
    fi
  done

  log "error: Kubernetes cluster is unreachable; no workflows were started."
  log "Fix the local cluster first, then rerun: ./scripts/run-added-workflows.sh $BATCH_DIR"
  log "Useful checks: kubectl --request-timeout=$KUBECTL_REQUEST_TIMEOUT get nodes"
  log "YAML-only validation still works with: .venv/bin/python -m pytest tests/integration/test_added_workflow_suite.py"
  exit 1
}

check_cluster

WORKFLOWS=()
while IFS= read -r workflow; do
  WORKFLOWS+=("$workflow")
done < <("$PYTHON" - <<'PY'
import ast
from pathlib import Path

source_path = Path("tests/integration/test_added_workflow_suite.py")
tree = ast.parse(source_path.read_text(), filename=str(source_path))
paths = None
for node in tree.body:
    if not isinstance(node, ast.Assign):
        continue
    for target in node.targets:
        if isinstance(target, ast.Name) and target.id == "ADDED_WORKFLOW_PATHS":
            paths = ast.literal_eval(node.value)
            break
    if paths is not None:
        break

if paths is None:
    raise SystemExit("ADDED_WORKFLOW_PATHS not found in test_added_workflow_suite.py")

for rel in sorted(paths, key=lambda p: (p.startswith("long/"), p)):
    print(str(Path("workflows") / rel))
PY
)

if [ "${#WORKFLOWS[@]}" -eq 0 ]; then
  echo "error: no workflows selected" >&2
  exit 1
fi

printf '%s\n' "${WORKFLOWS[@]}" > "$BATCH_DIR/workflows.txt"
cat > "$BATCH_DIR/manifest.json" <<EOF
{
  "agent": "$AGENT",
  "sandbox": "$SANDBOX",
  "max_attempts": $MAX_ATTEMPTS,
  "stage_failure_mode": "$STAGE_FAILURE_MODE",
  "final_sweep_mode": "$FINAL_SWEEP_MODE",
  "reset_namespaces": "$RESET_NAMESPACES",
  "reset_namespace_list": "$RESET_NAMESPACE_LIST",
  "workflow_count": ${#WORKFLOWS[@]},
  "workflows_file": "workflows.txt",
  "progress_file": "batch-progress.jsonl",
  "log_file": "batch.log"
}
EOF

# : > "$LOG_FILE"
# : > "$PROGRESS_FILE"
touch "$LOG_FILE"
touch "$PROGRESS_FILE"

echo "Batch directory: $BATCH_DIR" | tee -a "$LOG_FILE"
echo "Workflow count: ${#WORKFLOWS[@]}" | tee -a "$LOG_FILE"
echo "Agent: $AGENT" | tee -a "$LOG_FILE"
echo "Sandbox: $SANDBOX" | tee -a "$LOG_FILE"
echo "Resume mode: completed workflows in this batch folder will be skipped." | tee -a "$LOG_FILE"
echo "Namespace reset: $RESET_NAMESPACES" | tee -a "$LOG_FILE"
echo "Results UI: open the Results tab and refresh; look for $(basename "$BATCH_DIR")." | tee -a "$LOG_FILE"

reset_workflow_namespaces() {
  if [ "$RESET_NAMESPACES" != "1" ]; then
    return 0
  fi

  if ! kubectl --request-timeout="$KUBECTL_REQUEST_TIMEOUT" get namespace default > /dev/null 2>&1; then
    echo "error: Kubernetes cluster became unreachable before namespace reset." | tee -a "$LOG_FILE"
    return 1
  fi

  echo "Resetting app namespaces before workflow: $RESET_NAMESPACE_LIST" | tee -a "$LOG_FILE"
  for ns in $RESET_NAMESPACE_LIST; do
    kubectl --request-timeout="$KUBECTL_REQUEST_TIMEOUT" delete namespace "$ns" --ignore-not-found=true --wait=false >/dev/null 2>&1 || true
  done
  for ns in $RESET_NAMESPACE_LIST; do
    kubectl --request-timeout="$KUBECTL_REQUEST_TIMEOUT" wait --for=delete "namespace/$ns" --timeout="$RESET_WAIT_TIMEOUT" >/dev/null 2>&1 || true
  done
}

failures=0
total="${#WORKFLOWS[@]}"
for index in "${!WORKFLOWS[@]}"; do
  workflow="${WORKFLOWS[$index]}"
  display_index=$((index + 1))
  started_at="$(date +%s)"

  if completed_run="$("$PYTHON" - "$BATCH_DIR" "$workflow" <<'PY'
import json
import sys
from pathlib import Path

batch_dir = Path(sys.argv[1])
workflow_id = Path(sys.argv[2]).stem

for config_path in sorted(batch_dir.glob("*/config.json"), reverse=True):
    try:
        config = json.loads(config_path.read_text())
    except Exception:
        continue
    if config.get("workflow_id") != workflow_id:
        continue

    run_dir = config_path.parent
    status = None
    for name in ("run.json", "workflow_state.json"):
        path = run_dir / name
        if not path.exists():
            continue
        try:
            status = json.loads(path.read_text()).get("status")
        except Exception:
            status = None
        if status:
            break

    if status == "complete":
        print(run_dir)
        raise SystemExit(0)

raise SystemExit(1)
PY
)"; then
    echo "" | tee -a "$LOG_FILE"
    echo "[$display_index/$total] SKIP $workflow (already complete: $completed_run)" | tee -a "$LOG_FILE"
    printf '{"index":%d,"total":%d,"workflow":"%s","skipped":true,"reason":"already_complete","run_dir":"%s"}\n' \
      "$display_index" "$total" "$workflow" "$completed_run" >> "$PROGRESS_FILE"
    continue
  fi

  echo "" | tee -a "$LOG_FILE"
  echo "[$display_index/$total] START $workflow" | tee -a "$LOG_FILE"
  reset_workflow_namespaces

  cmd=(
    "$PYTHON" orchestrator.py run-workflow "$workflow"
    --agent "$AGENT"
    --sandbox "$SANDBOX"
    --runs-dir "$BATCH_DIR"
    --max-attempts "$MAX_ATTEMPTS"
    --stage-failure-mode "$STAGE_FAILURE_MODE"
    --final-sweep-mode "$FINAL_SWEEP_MODE"
  )
  if [ -n "$SETUP_TIMEOUT" ]; then
    cmd+=(--setup-timeout "$SETUP_TIMEOUT")
  fi
  if [ -n "$VERIFY_TIMEOUT" ]; then
    cmd+=(--verify-timeout "$VERIFY_TIMEOUT")
  fi
  if [ -n "$CLEANUP_TIMEOUT" ]; then
    cmd+=(--cleanup-timeout "$CLEANUP_TIMEOUT")
  fi

  set +e
  "${cmd[@]}" 2>&1 | tee -a "$LOG_FILE"
  rc="${PIPESTATUS[0]}"
  set -e

  elapsed=$(( $(date +%s) - started_at ))
  if [ "$rc" -ne 0 ]; then
    failures=$((failures + 1))
  fi

  printf '{"index":%d,"total":%d,"workflow":"%s","returncode":%d,"elapsed_sec":%d}\n' \
    "$display_index" "$total" "$workflow" "$rc" "$elapsed" >> "$PROGRESS_FILE"
  echo "[$display_index/$total] END $workflow rc=$rc elapsed=${elapsed}s" | tee -a "$LOG_FILE"

  if [ "$rc" -ne 0 ] && [ "$STOP_ON_FAILURE" = "1" ]; then
    echo "Stopping after first command failure because KARMA_STOP_ON_FAILURE=1." | tee -a "$LOG_FILE"
    exit "$rc"
  fi
done

echo "" | tee -a "$LOG_FILE"
echo "DONE batch_dir=$BATCH_DIR workflows=$total command_failures=$failures" | tee -a "$LOG_FILE"
exit 0
