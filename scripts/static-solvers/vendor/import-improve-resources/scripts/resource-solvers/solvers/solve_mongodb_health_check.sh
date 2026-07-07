#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
kubectl -n "$ns" patch configmap health-overrides --type=json \
  -p="[{\"op\":\"remove\",\"path\":\"/data/${cluster}-1\"}]" 2>/dev/null || \
  kubectl -n "$ns" patch configmap health-overrides --type=merge -p='{"data":{}}'
kubectl -n "$ns" delete pod "${cluster}-1" --ignore-not-found=true --wait=false
kubectl -n "$ns" wait --for=condition=ready "pod/${cluster}-1" --timeout=600s
printf 'repaired health override\n' > submit.txt
