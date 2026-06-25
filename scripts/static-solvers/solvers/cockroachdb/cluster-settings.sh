#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/cluster-settings
# Strategy: parameter_aware_shell
# Imported reference: cockroachdb/cluster-settings
# Notes: Current workflows override both setting name and value, and some use
# the legacy snapshot-recovery setting name that must fall back to the live
# cluster's accepted equivalent.

static_solver_export_namespace_if_unset "cockroachdb"

prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
setting_name="${BENCH_PARAM_SETTING_NAME:-kv.snapshot_rebalance.max_rate}"
setting_value="${BENCH_PARAM_SETTING_VALUE:-128MiB}"

cluster_flag="--insecure"
if kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
  ls /cockroach/cockroach-certs/ca.crt >/dev/null 2>&1; then
  cluster_flag="--certs-dir=/cockroach/cockroach-certs"
fi

sql_value_expr() {
  local raw="${1:?value is required}"
  local lowered
  lowered="$(printf '%s' "${raw}" | tr '[:upper:]' '[:lower:]')"
  case "${lowered}" in
    true|false|on|off|yes|no)
      printf '%s\n' "${lowered}"
      ;;
    *)
      printf "'%s'\n" "${raw//\'/\'\'}"
      ;;
  esac
}

setting_alias() {
  local current="${1:?setting name is required}"
  case "${current}" in
    kv.snapshot_recovery.max_rate)
      printf 'kv.snapshot_rebalance.max_rate\n'
      ;;
    kv.snapshot_rebalance.max_rate)
      printf 'kv.snapshot_recovery.max_rate\n'
      ;;
    *)
      printf '\n'
      ;;
  esac
}

run_setting_sql() {
  local current_setting="${1:?setting name is required}"
  local value_expr="${2:?value expression is required}"
  kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
    ./cockroach sql "${cluster_flag}" \
    -e "SET CLUSTER SETTING ${current_setting} = ${value_expr};"
}

value_expr="$(sql_value_expr "${setting_value}")"

if ! output="$(run_setting_sql "${setting_name}" "${value_expr}" 2>&1)"; then
  alias_name="$(setting_alias "${setting_name}")"
  if [[ -n "${alias_name}" ]] && grep -Eqi "unknown( cluster)? setting" <<<"${output}"; then
    static_solver_log "falling back from ${setting_name} to ${alias_name}"
    run_setting_sql "${alias_name}" "${value_expr}" >/dev/null
  else
    printf '%s\n' "${output}" >&2
    exit 1
  fi
fi

static_solver_write_submit "updated CockroachDB cluster setting"
