#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/readonly-audit
# Strategy: state_capture_shell
# Notes: Produce a read-only compliance report as a ConfigMap.

static_solver_export_namespace_if_unset "cockroachdb"

prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
report_path="${STATIC_SOLVER_STAGE_DIR}/findings.txt"

cluster_flag="--insecure"
if kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
  ls /cockroach/cockroach-certs/ca.crt >/dev/null 2>&1; then
  cluster_flag="--certs-dir=/cockroach/cockroach-certs"
fi

node_status="$(
  kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
    ./cockroach node status "${cluster_flag}" 2>&1 || true
)"

non_default_settings="$(
  kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
    ./cockroach sql "${cluster_flag}" --format=tsv \
    -e "SELECT variable, value FROM crdb_internal.cluster_settings WHERE value != default_value;" \
    2>&1 || true
)"

zone_configs="$(
  kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
    ./cockroach sql "${cluster_flag}" --format=tsv \
    -e "SELECT target, raw_config_sql FROM crdb_internal.zones;" \
    2>&1 || true
)"

statefulset_info="$(
  kubectl -n "${BENCH_NAMESPACE}" get statefulset "${prefix}" -o yaml 2>&1 || true
)"

pod_overview="$(
  kubectl -n "${BENCH_NAMESPACE}" get pods -o wide 2>&1 || true
)"

{
  printf 'CockroachDB Read-Only Audit\n'
  printf 'Namespace: %s\n' "${BENCH_NAMESPACE}"
  printf 'Cluster prefix: %s\n' "${prefix}"
  printf '\n=== Node Status ===\n%s\n' "${node_status}"
  printf '\n=== Non-Default Cluster Settings ===\n%s\n' "${non_default_settings}"
  printf '\n=== Zone Configurations ===\n%s\n' "${zone_configs}"
  printf '\n=== StatefulSet ===\n%s\n' "${statefulset_info}"
  printf '\n=== Pod Overview ===\n%s\n' "${pod_overview}"
} > "${report_path}"

kubectl -n "${BENCH_NAMESPACE}" create configmap config-audit \
  --from-file=findings.txt="${report_path}" \
  --dry-run=client -o yaml | kubectl apply -f -

static_solver_write_submit "prepared config-audit ConfigMap"
