#!/usr/bin/env bash
#
# rollback.sh — Revert non-default CockroachDB configuration to v24.1 defaults.
#
# PURPOSE
#   Restore the cockroachdb cluster's customized cluster settings and zone
#   configurations back to CockroachDB v24.1 factory defaults during the
#   approved change window.
#
# WARNING
#   This UNDOES configuration the cluster currently depends on. Run ONLY during
#   the scheduled change window after review. Do not run as part of review.
#
# SCOPE (derived from inspecting the live cluster on 2026-06-18)
#   Cluster settings reverted to default:
#     - diagnostics.reporting.enabled : true   -> false (default)
#     - kv.snapshot_rebalance.max_rate: 1.0 MiB -> 32 MiB (default)
#   Zone configs reverted to default:
#     - RANGE default / RANGE system / DATABASE system:
#         gc.ttlseconds 14400 -> 90000 (v24.1 default)
#
#   NOT touched (already at v24.1 defaults, so reverting them would be a no-op
#   or could unexpectedly change cluster behavior):
#     - cluster.secret, version  (auto-managed, not operator-tunable)
#     - RANGE meta (gc=3600), RANGE liveness (gc=600), system-table subzones
#     - num_replicas (3 for default range, 5 for system ranges)
#     - range_min_bytes / range_max_bytes (128 MiB / 512 MiB)
#
set -euo pipefail

NS="${NS:-cockroachdb}"
POD="${POD:-crdb-cluster-0}"

sql() {
  kubectl -n "$NS" exec "$POD" -- ./cockroach sql --insecure -e "$1"
}

echo ">> Reverting non-default cluster settings to defaults"
sql "RESET CLUSTER SETTING diagnostics.reporting.enabled;"
sql "RESET CLUSTER SETTING kv.snapshot_rebalance.max_rate;"

echo ">> Reverting customized zone gc.ttlseconds back to the 90000 default"
sql "ALTER RANGE default   CONFIGURE ZONE USING gc.ttlseconds = 90000;"
sql "ALTER RANGE system    CONFIGURE ZONE USING gc.ttlseconds = 90000;"
sql "ALTER DATABASE system CONFIGURE ZONE USING gc.ttlseconds = 90000;"

echo ">> Rollback complete. Verify with:"
echo "   kubectl -n $NS exec $POD -- ./cockroach sql --insecure -e \"SELECT variable, value FROM crdb_internal.cluster_settings WHERE value != default_value;\""
echo "   kubectl -n $NS exec $POD -- ./cockroach sql --insecure -e \"SELECT target, raw_config_sql FROM crdb_internal.zones;\""
