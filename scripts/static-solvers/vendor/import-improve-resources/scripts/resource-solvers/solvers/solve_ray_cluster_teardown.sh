#!/bin/sh
set -eu

prefix="${BENCH_PARAM_CLUSTER_PREFIX:-ray}"
kubectl -n "$BENCH_NAMESPACE" delete deployment \
  "${prefix}-head" "${prefix}-worker" --ignore-not-found=true --wait=true
kubectl -n "$BENCH_NAMESPACE" delete service \
  "${prefix}-head" --ignore-not-found=true --wait=true
printf 'removed Ray cluster resources\n' > submit.txt
