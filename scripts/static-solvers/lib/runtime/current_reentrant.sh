#!/usr/bin/env bash
set -euo pipefail

STATIC_SOLVER_STAGE_IDS=()
STATIC_SOLVER_SOLVER_PATHS=()

plan_stage() {
  local stage_id="${1:?stage id is required}"
  local solver_rel="${2:?solver path is required}"
  STATIC_SOLVER_STAGE_IDS+=("${stage_id}")
  STATIC_SOLVER_SOLVER_PATHS+=("${solver_rel}")
}

dispatch_current_stage() {
  local stage_id="${1:-${STATIC_SOLVER_STAGE_ID}}"
  local solver_rel=""
  local i

  for i in "${!STATIC_SOLVER_STAGE_IDS[@]}"; do
    if [[ "${STATIC_SOLVER_STAGE_IDS[$i]}" == "${stage_id}" ]]; then
      solver_rel="${STATIC_SOLVER_SOLVER_PATHS[$i]}"
      break
    fi
  done

  if [[ -z "${solver_rel}" ]]; then
    static_solver_fail "no static solver entry for stage ${stage_id}"
  fi

  local solver_path="${STATIC_SOLVER_ROOT}/solvers/${solver_rel}"
  [[ -f "${solver_path}" ]] || static_solver_fail "missing active solver script: ${solver_path}"

  static_solver_log "dispatching stage ${stage_id} via ${solver_rel}"
  bash "${solver_path}"
}
