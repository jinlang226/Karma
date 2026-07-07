#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=../lib/common.sh
source "${SCRIPT_DIR}/../lib/common.sh"
# shellcheck source=../lib/runtime/current_reentrant.sh
source "${SCRIPT_DIR}/../lib/runtime/current_reentrant.sh"

workflow_arg="${1:?usage: run_current_workflow.sh <workflow.yaml>}"
plan_path="$(static_solver_plan_path_from_workflow "${workflow_arg}")"

[[ -f "${plan_path}" ]] || static_solver_fail "missing workflow plan: ${plan_path}"

static_solver_log "workflow=${workflow_arg} stage=${STATIC_SOLVER_STAGE_ID} plan=${plan_path}"

# shellcheck source=/dev/null
source "${plan_path}"
dispatch_current_stage "${STATIC_SOLVER_STAGE_ID}"
