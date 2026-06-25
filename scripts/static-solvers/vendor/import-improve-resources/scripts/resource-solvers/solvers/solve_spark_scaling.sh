#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-spark}"
target="${BENCH_PARAM_TARGET_WORKER_REPLICAS:-2}"
kubectl -n "$ns" scale "deployment/${prefix}-worker" --replicas="$target"
kubectl -n "$ns" rollout status "deployment/${prefix}-worker" --timeout=300s
printf 'scaled Spark workers\n' > submit.txt
