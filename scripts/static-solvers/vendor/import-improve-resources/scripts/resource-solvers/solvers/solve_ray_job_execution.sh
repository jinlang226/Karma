#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-ray}"
sed -e "s/__CLUSTER_PREFIX__/${prefix}/g" \
  resources/ray/job_execution/resource/ray-job-runner.yaml |
  kubectl -n "$ns" apply -f -
kubectl -n "$ns" wait --for=condition=complete \
  "job/${prefix}-job-runner" --timeout=300s
output=$(kubectl -n "$ns" logs "job/${prefix}-job-runner" | tail -n1)
result=$(printf '%s\n' "$output" |
  python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["message"])')
kubectl -n "$ns" create configmap "${prefix}-job-result" \
  --from-literal=result="$result" --dry-run=client -o yaml |
  kubectl -n "$ns" apply -f -
printf 'executed Ray job\n' > submit.txt
