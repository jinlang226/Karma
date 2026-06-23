#!/usr/bin/env bash
set -euo pipefail

# Rollback rehearsal generated from the live cockroachdb namespace on 2026-06-23.
#
# Current inspection summary:
# - Public cluster-setting overrides:
#   * diagnostics.reporting.enabled: true -> default false
#   * kv.snapshot_rebalance.max_rate: 64 MiB -> default 32 MiB
# - crdb_internal.cluster_settings also reports cluster.secret and version as
#   overrides. Those are internal cluster identity/version values, not rollback
#   targets, and are intentionally not reset here.
# - Live zone configs matched a pristine 3-node CockroachDB v24.1.0 instance, so
#   no zone-config rollback SQL is required.
# - StatefulSet crdb-cluster currently has 3 replicas, matching the applied
#   default manifest, so no replica scaling is required.
#
# This script is intentionally guarded so review/storage does not accidentally
# mutate the live cluster. Execute only during the approved change window.

NAMESPACE="${NAMESPACE:-cockroachdb}"
SQL_POD="${SQL_POD:-crdb-cluster-0}"

if [[ "${1:-}" != "--execute" ]]; then
  cat <<'USAGE'
Rollback script is in review mode and has not changed the cluster.

To execute during the approved change window:
  ./rollback.sh --execute

Optional environment overrides:
  NAMESPACE=cockroachdb
  SQL_POD=crdb-cluster-0
USAGE
  exit 2
fi

command -v kubectl >/dev/null 2>&1

echo "Resetting public CockroachDB cluster-setting overrides to defaults..."
kubectl -n "${NAMESPACE}" exec "${SQL_POD}" -- ./cockroach sql --insecure \
  -e "RESET CLUSTER SETTING diagnostics.reporting.enabled" \
  -e "RESET CLUSTER SETTING kv.snapshot_rebalance.max_rate"

echo "Verifying rollback targets..."
kubectl -n "${NAMESPACE}" exec "${SQL_POD}" -- ./cockroach sql --insecure --format=table \
  -e "SELECT variable, value, default_value, origin FROM crdb_internal.cluster_settings WHERE variable IN ('diagnostics.reporting.enabled', 'kv.snapshot_rebalance.max_rate') ORDER BY variable;"

echo "Rollback complete."
