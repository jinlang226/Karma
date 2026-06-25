#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
worker="${BENCH_PARAM_TRANSFORM_CLUSTER_PREFIX:-es-transform}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
transform="${BENCH_PARAM_TRANSFORM_ID:-events-by-service}"

kubectl -n "$ns" scale "statefulset/$worker" --replicas=1
kubectl -n "$ns" wait --for=condition=ready pod -l "app=$worker" --timeout=900s
kubectl -n "$ns" exec curl-test -- curl -sS -XPOST \
  "http://$service:9200/_transform/$transform/_start" >/dev/null
printf 'restored transform capacity and started transform\n' > submit.txt
