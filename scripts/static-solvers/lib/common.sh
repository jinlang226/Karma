#!/usr/bin/env bash
set -euo pipefail

STATIC_SOLVER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATIC_SOLVER_ROOT="$(cd "${STATIC_SOLVER_LIB_DIR}/.." && pwd)"
STATIC_SOLVER_REPO_ROOT="$(cd "${STATIC_SOLVER_ROOT}/../.." && pwd)"
STATIC_SOLVER_VENDOR_ROOT="${STATIC_SOLVER_ROOT}/vendor/import-improve-resources"
STATIC_SOLVER_STAGE_DIR="${PWD}"
STATIC_SOLVER_STAGE_ID="$(basename "${STATIC_SOLVER_STAGE_DIR}")"
STATIC_SOLVER_SUBMIT_FILE="${STATIC_SOLVER_STAGE_DIR}/submit.txt"

export BENCHMARK_SUBMIT_FILE="${STATIC_SOLVER_SUBMIT_FILE}"

static_solver_log() {
  printf '[static-solver] %s\n' "$*" >&2
}

static_solver_fail() {
  static_solver_log "error: $*"
  exit 1
}

static_solver_write_submit() {
  local message="${1:-submitted static solver}"
  printf '%s\n' "${message}" > "${STATIC_SOLVER_SUBMIT_FILE}"
}

static_solver_export_namespace_if_unset() {
  local namespace="${1:?namespace is required}"
  if [[ -z "${BENCH_NAMESPACE:-}" ]]; then
    export BENCH_NAMESPACE="${namespace}"
  fi
}

static_solver_export_nginx_defaults() {
  local app_namespace="${1:-demo}"
  local ingress_namespace="${2:-ingress-nginx}"
  local otel_namespace="${3:-}"

  static_solver_export_namespace_if_unset "${app_namespace}"
  export BENCH_NS_APP="${BENCH_NS_APP:-${BENCH_NAMESPACE}}"
  export BENCH_NS_INGRESS="${BENCH_NS_INGRESS:-${ingress_namespace}}"
  if [[ -n "${otel_namespace}" ]]; then
    export BENCH_NS_OTEL="${BENCH_NS_OTEL:-${otel_namespace}}"
  fi
}

static_solver_ensure_vendor_resources_link() {
  local link_path="${STATIC_SOLVER_STAGE_DIR}/resources"
  local target_path="${STATIC_SOLVER_VENDOR_ROOT}/resources"

  if [[ -L "${link_path}" ]]; then
    rm -f "${link_path}"
  elif [[ -e "${link_path}" ]]; then
    static_solver_fail "stage path ${link_path} already exists and is not a symlink"
  fi

  ln -s "${target_path}" "${link_path}"
}

static_solver_resolve_workflow_path() {
  local raw_path="${1:?workflow path is required}"
  local candidate=""

  if [[ "${raw_path}" = /* ]]; then
    candidate="${raw_path}"
  elif [[ -f "${STATIC_SOLVER_REPO_ROOT}/${raw_path}" ]]; then
    candidate="${STATIC_SOLVER_REPO_ROOT}/${raw_path}"
  elif [[ -f "${STATIC_SOLVER_REPO_ROOT}/workflows/${raw_path}" ]]; then
    candidate="${STATIC_SOLVER_REPO_ROOT}/workflows/${raw_path}"
  else
    static_solver_fail "cannot resolve workflow path: ${raw_path}"
  fi

  if [[ ! -f "${candidate}" ]]; then
    static_solver_fail "workflow file does not exist: ${candidate}"
  fi

  printf '%s\n' "${candidate}"
}

static_solver_plan_path_from_workflow() {
  local workflow_path
  workflow_path="$(static_solver_resolve_workflow_path "$1")"
  local prefix="${STATIC_SOLVER_REPO_ROOT}/workflows/"
  if [[ "${workflow_path}" != "${prefix}"* ]]; then
    local source_workflow=""
    source_workflow="$(python3 - "${workflow_path}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text())
except Exception:
    payload = {}
source = ((payload.get("metadata") or {}).get("source_workflow") or "")
print(source)
PY
)"
    if [[ -n "${source_workflow}" ]]; then
      workflow_path="$(static_solver_resolve_workflow_path "${source_workflow}")"
    fi
  fi
  if [[ "${workflow_path}" != "${prefix}"* ]]; then
    static_solver_fail "workflow is outside workflows/: ${workflow_path}"
  fi
  local rel_path="${workflow_path#${prefix}}"
  printf '%s\n' "${STATIC_SOLVER_ROOT}/plans/workflows/${rel_path%.yaml}.sh"
}

static_solver_run_vendored_shell() {
  local rel_solver_path="${1:?vendored solver path is required}"
  local solver_path="${STATIC_SOLVER_ROOT}/${rel_solver_path}"

  [[ -f "${solver_path}" ]] || static_solver_fail "missing vendored shell solver: ${solver_path}"

  static_solver_ensure_vendor_resources_link
  static_solver_log "running vendored shell solver ${rel_solver_path}"
  /bin/sh "${solver_path}"

  [[ -f "${STATIC_SOLVER_SUBMIT_FILE}" ]] || static_solver_fail "solver did not create submit.txt"
}

static_solver_run_vendored_resource_python() {
  local resource_case="${1:?resource case is required}"
  local submit_message="${2:-submitted static solver}"
  local solve_py="${STATIC_SOLVER_VENDOR_ROOT}/resources/${resource_case}/solver/solve.py"

  [[ -f "${solve_py}" ]] || static_solver_fail "missing vendored Python solver: ${solve_py}"

  static_solver_ensure_vendor_resources_link
  static_solver_log "running vendored Python solver ${resource_case}"
  python3 "${solve_py}"
  static_solver_write_submit "${submit_message}"
}

static_solver_submit_only() {
  local submit_message="${1:-submitted static solver}"
  static_solver_log "submit-only solver path"
  static_solver_write_submit "${submit_message}"
}
