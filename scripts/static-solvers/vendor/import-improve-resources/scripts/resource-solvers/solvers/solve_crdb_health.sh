#!/bin/sh
set -eu
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
envsubst '${BENCH_PARAM_CLUSTER_PREFIX} ${BENCH_PARAM_REPLICA_COUNT}' \
  < resources/cockroachdb/health-check-recovery/resource/statefulset.yaml |
  kubectl -n "$BENCH_NAMESPACE" apply -f -
kubectl -n "$BENCH_NAMESPACE" rollout status "statefulset/${prefix}" --timeout=900s
printf 'repaired CockroachDB health checks\n' > submit.txt
