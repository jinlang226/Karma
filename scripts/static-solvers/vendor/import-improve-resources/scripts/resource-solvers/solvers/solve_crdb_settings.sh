#!/bin/sh
set -eu
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
setting="${BENCH_PARAM_SETTING_NAME:-kv.snapshot_rebalance.max_rate}"
kubectl -n "$BENCH_NAMESPACE" exec "${prefix}-0" -- \
  ./cockroach sql --insecure -e "SET CLUSTER SETTING ${setting} = '128MiB';"
printf 'updated CockroachDB cluster setting\n' > submit.txt
