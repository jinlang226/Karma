#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-ray}"
image="${BENCH_PARAM_RAY_IMAGE:-rayproject/ray:2.9.0}"
workers="${BENCH_PARAM_WORKER_REPLICAS:-2}"
sed -e "s/__CLUSTER_PREFIX__/${prefix}/g" \
  resources/ray/cluster_ready/resource/ray-head-service.yaml |
  kubectl -n "$ns" apply -f -
sed -e "s/__CLUSTER_PREFIX__/${prefix}/g" -e "s#__RAY_IMAGE__#${image}#g" \
  resources/ray/cluster_ready/resource/ray-head.yaml |
  kubectl -n "$ns" apply -f -
sed -e "s/__CLUSTER_PREFIX__/${prefix}/g" -e "s#__RAY_IMAGE__#${image}#g" \
  -e "s/__WORKER_REPLICAS__/${workers}/g" \
  resources/ray/cluster_ready/resource/ray-worker.yaml |
  kubectl -n "$ns" apply -f -
kubectl -n "$ns" rollout status "deployment/${prefix}-head" --timeout=300s
kubectl -n "$ns" rollout status "deployment/${prefix}-worker" --timeout=300s
printf 'converged Ray cluster\n' > submit.txt
