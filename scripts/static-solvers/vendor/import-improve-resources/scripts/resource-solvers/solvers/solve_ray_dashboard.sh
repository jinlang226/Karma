#!/bin/sh
set -eu

prefix="${BENCH_PARAM_CLUSTER_PREFIX:-ray}"
port="${BENCH_PARAM_DASHBOARD_PORT:-8265}"
service="${prefix}-head"
if ! kubectl -n "$BENCH_NAMESPACE" get service "$service" \
  -o jsonpath='{range .spec.ports[*]}{.port}{"\n"}{end}' | grep -qx "$port"; then
  kubectl -n "$BENCH_NAMESPACE" patch service "$service" --type=json \
    -p="[{\"op\":\"add\",\"path\":\"/spec/ports/-\",\"value\":{\"name\":\"dashboard-${port}\",\"port\":${port},\"protocol\":\"TCP\",\"targetPort\":8265}}]"
fi
printf 'exposed Ray dashboard\n' > submit.txt
