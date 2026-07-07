#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/zone-config
# Strategy: direct_shell
# Notes: Apply tenant-specific zone configs while handling both insecure and
# secure CockroachDB clusters inherited from earlier workflow stages.

static_solver_export_namespace_if_unset "cockroachdb"

prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
schema="${BENCH_PARAM_TARGET_SCHEMA:-tenant_b}"
replicas="${BENCH_PARAM_NUM_REPLICAS:-3}"
ttl="${BENCH_PARAM_GC_TTL_SECONDS:-14400}"
range_min="${BENCH_PARAM_RANGE_MIN_BYTES:-134217728}"
range_max="${BENCH_PARAM_RANGE_MAX_BYTES:-536870912}"

cluster_flag="--insecure"
if kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
  ls /cockroach/cockroach-certs/ca.crt >/dev/null 2>&1; then
  cluster_flag="--certs-dir=/cockroach/cockroach-certs"
fi

tables="$(
  kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
    ./cockroach sql "${cluster_flag}" --database=defaultdb --format=tsv -e \
    "SELECT table_name FROM information_schema.tables WHERE table_schema='${schema}' AND table_type='BASE TABLE' ORDER BY table_name;" \
    2>/dev/null
)"

applied=0
while IFS=$'\t' read -r table _; do
  [[ -z "${table}" ]] && continue
  [[ "${table}" == "table_name" ]] && continue
  kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
    ./cockroach sql "${cluster_flag}" --database=defaultdb -e \
    "ALTER TABLE ${schema}.${table} CONFIGURE ZONE USING num_replicas=${replicas}, gc.ttlseconds=${ttl}, range_min_bytes=${range_min}, range_max_bytes=${range_max};" \
    >/dev/null
  applied=1
done <<< "${tables}"

if [[ "${applied}" -eq 0 ]]; then
  static_solver_fail "no base tables found in schema ${schema}"
fi

static_solver_write_submit "configured CockroachDB tenant zones"
