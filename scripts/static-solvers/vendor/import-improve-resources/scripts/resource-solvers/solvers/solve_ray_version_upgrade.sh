#!/bin/sh
set -eu

prefix="${BENCH_PARAM_CLUSTER_PREFIX:-ray}"
image="${BENCH_PARAM_TO_IMAGE:-rayproject/ray:2.9.0}"
kubectl -n "$BENCH_NAMESPACE" set image \
  "deployment/${prefix}-head" "ray-head=${image}"
kubectl -n "$BENCH_NAMESPACE" set image \
  "deployment/${prefix}-worker" "ray-worker=${image}"
kubectl -n "$BENCH_NAMESPACE" rollout status \
  "deployment/${prefix}-head" --timeout=300s
kubectl -n "$BENCH_NAMESPACE" rollout status \
  "deployment/${prefix}-worker" --timeout=300s
printf 'upgraded Ray cluster\n' > submit.txt
