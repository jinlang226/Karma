#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
to="${BENCH_PARAM_TO_VERSION:-24.1.1}"
kubectl -n "$ns" patch "statefulset/${prefix}" --type=merge \
  -p='{"spec":{"updateStrategy":{"type":"RollingUpdate","rollingUpdate":{"partition":0}}}}'
kubectl -n "$ns" set image "statefulset/${prefix}" "db=cockroachdb/cockroach:v${to}"
kubectl -n "$ns" rollout status "statefulset/${prefix}" --timeout=1200s
printf 'completed CockroachDB rolling update\n' > submit.txt
