#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/version-check
# Strategy: native_shell
# Notes: Records the live feature version into the workflow-configured ConfigMap
# key and works for both insecure and inherited secure clusters.

static_solver_export_namespace_if_unset "cockroachdb"
static_solver_export_cockroachdb_defaults

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX}"
report_configmap_name="${BENCH_PARAM_REPORT_CONFIGMAP_NAME:-crdb-version-report}"
report_key="${BENCH_PARAM_REPORT_KEY:-db_version}"
conn_flag=(--insecure)

if kubectl -n "${ns}" exec "${prefix}-0" -- ls /cockroach/cockroach-certs/ca.crt >/dev/null 2>&1; then
  conn_flag=(--certs-dir=/cockroach/cockroach-certs)
fi

version="$(
  kubectl -n "${ns}" exec "${prefix}-0" -- \
    ./cockroach sql "${conn_flag[@]}" --format=tsv \
    -e 'SHOW CLUSTER SETTING version;' | tail -n1 | tr -d '\r'
)"

[[ -n "${version}" ]] || static_solver_fail "failed to read CockroachDB cluster version"

kubectl -n "${ns}" create configmap "${report_configmap_name}" \
  --from-literal="${report_key}=${version}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "recorded CockroachDB active feature version"
