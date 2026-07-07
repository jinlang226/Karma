#!/bin/sh
set -eu

prefix="${BENCH_PARAM_CLUSTER_PREFIX:-ray}"
target="${BENCH_PARAM_TARGET_WORKER_REPLICAS:-3}"
kubectl -n "$BENCH_NAMESPACE" scale \
  "deployment/${prefix}-worker" --replicas="$target"
kubectl -n "$BENCH_NAMESPACE" rollout status \
  "deployment/${prefix}-worker" --timeout=300s
printf 'scaled Ray workers\n' > submit.txt
