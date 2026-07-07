#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
from="${BENCH_PARAM_FROM_REPLICA_COUNT:-4}"
to="${BENCH_PARAM_TO_REPLICA_COUNT:-3}"
for ordinal in $(seq "$to" $((from - 1))); do
  node_id=$(kubectl -n "$ns" exec "${prefix}-0" -- ./cockroach node status \
    --insecure --format=tsv |
    awk -F '\t' -v pod="${prefix}-${ordinal}" 'NR>1 && index($2,pod){print $1; exit}')
  [ -n "$node_id" ]
  kubectl -n "$ns" exec "${prefix}-0" -- ./cockroach node decommission "$node_id" \
    --insecure --wait=all
done
kubectl -n "$ns" scale "statefulset/${prefix}" --replicas="$to"
kubectl -n "$ns" rollout status "statefulset/${prefix}" --timeout=900s
printf 'decommissioned CockroachDB nodes\n' > submit.txt
