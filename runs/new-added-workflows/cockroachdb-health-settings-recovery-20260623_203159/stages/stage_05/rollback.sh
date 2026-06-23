#!/usr/bin/env bash
set -euo pipefail

# Rollback script prepared from the cockroachdb namespace on 2026-06-23.
# It is intended for review and later execution in a change window.
#
# Intentionally not reset:
# - cluster.secret: internal, cluster-specific telemetry anonymization secret.
# - version: cluster lifecycle/version gate, not an operator tuning setting.

NAMESPACE="${NAMESPACE:-cockroachdb}"
SQL_POD="${SQL_POD:-crdb-cluster-0}"
STATEFULSET="${STATEFULSET:-crdb-cluster}"
COCKROACH_BIN="${COCKROACH_BIN:-/cockroach/cockroach}"

echo "Resetting public non-default cluster settings to CockroachDB defaults..."
kubectl -n "$NAMESPACE" exec -i "$SQL_POD" -- "$COCKROACH_BIN" sql --insecure <<'SQL'
RESET CLUSTER SETTING diagnostics.reporting.enabled;
RESET CLUSTER SETTING kv.snapshot_rebalance.max_rate;
SQL

echo "Restoring CockroachDB v24.1.0 default zone configurations..."
kubectl -n "$NAMESPACE" exec -i "$SQL_POD" -- "$COCKROACH_BIN" sql --insecure <<'SQL'
ALTER RANGE default CONFIGURE ZONE USING
  range_min_bytes = 134217728,
  range_max_bytes = 536870912,
  gc.ttlseconds = 14400,
  num_replicas = 3,
  constraints = '[]',
  lease_preferences = '[]';

ALTER DATABASE system CONFIGURE ZONE USING
  range_min_bytes = 134217728,
  range_max_bytes = 536870912,
  gc.ttlseconds = 14400,
  num_replicas = 5,
  constraints = '[]',
  lease_preferences = '[]';

ALTER RANGE system CONFIGURE ZONE USING
  range_min_bytes = 134217728,
  range_max_bytes = 536870912,
  gc.ttlseconds = 14400,
  num_replicas = 5,
  constraints = '[]',
  lease_preferences = '[]';

ALTER RANGE meta CONFIGURE ZONE USING
  range_min_bytes = 134217728,
  range_max_bytes = 536870912,
  gc.ttlseconds = 3600,
  num_replicas = 5,
  constraints = '[]',
  lease_preferences = '[]';

ALTER RANGE liveness CONFIGURE ZONE USING
  range_min_bytes = 134217728,
  range_max_bytes = 536870912,
  gc.ttlseconds = 600,
  num_replicas = 5,
  constraints = '[]',
  lease_preferences = '[]';

ALTER TABLE system.public.lease CONFIGURE ZONE USING
  gc.ttlseconds = 600;

ALTER TABLE system.public.replication_constraint_stats CONFIGURE ZONE USING
  gc.ttlseconds = 600;

ALTER TABLE system.public.replication_stats CONFIGURE ZONE USING
  gc.ttlseconds = 600;

ALTER TABLE system.public.span_stats_tenant_boundaries CONFIGURE ZONE USING
  gc.ttlseconds = 3600;

ALTER TABLE system.public.statement_activity CONFIGURE ZONE USING
  gc.ttlseconds = 3600;

ALTER TABLE system.public.statement_statistics CONFIGURE ZONE USING
  gc.ttlseconds = 3600;

ALTER TABLE system.public.tenant_usage CONFIGURE ZONE USING
  gc.ttlseconds = 7200;

ALTER TABLE system.public.transaction_activity CONFIGURE ZONE USING
  gc.ttlseconds = 3600;

ALTER TABLE system.public.transaction_statistics CONFIGURE ZONE USING
  gc.ttlseconds = 3600;
SQL

echo "Restoring StatefulSet replica count to the reviewed default target..."
kubectl -n "$NAMESPACE" scale statefulset "$STATEFULSET" --replicas=3

echo "Rollback complete. Review current settings with:"
echo "  kubectl -n $NAMESPACE exec $SQL_POD -- $COCKROACH_BIN sql --insecure -e \"SELECT variable, value FROM crdb_internal.cluster_settings WHERE value != default_value;\""
echo "  kubectl -n $NAMESPACE exec $SQL_POD -- $COCKROACH_BIN sql --insecure -e \"SELECT target, raw_config_sql FROM crdb_internal.zones;\""
echo "  kubectl -n $NAMESPACE get statefulset $STATEFULSET"
