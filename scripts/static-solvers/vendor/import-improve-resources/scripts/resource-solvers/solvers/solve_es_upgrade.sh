#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
version="${BENCH_PARAM_TO_VERSION:-8.11.1}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"

kubectl -n "$ns" scale "statefulset/$prefix" --replicas=0
kubectl -n "$ns" wait --for=delete pod -l "app=$prefix" --timeout=600s
kubectl -n "$ns" set image "statefulset/$prefix" "elasticsearch=docker.elastic.co/elasticsearch/elasticsearch:$version"
kubectl -n "$ns" scale "statefulset/$prefix" --replicas=3
kubectl -n "$ns" wait --for=condition=ready pod -l "app=$prefix" --timeout=1200s

for i in $(seq 1 60); do
  root=$(kubectl -n "$ns" exec curl-test -- curl -fsS --max-time 5 "http://${service}:9200/" 2>/dev/null || true)
  health=$(kubectl -n "$ns" exec curl-test -- curl -fsS --max-time 5 \
    "http://${service}:9200/_cluster/health?wait_for_status=yellow&wait_for_nodes=3&timeout=5s" 2>/dev/null || true)
  current=$(printf '%s' "$root" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"]["number"])' 2>/dev/null || true)
  nodes=$(printf '%s' "$health" | python3 -c 'import json,sys; print(json.load(sys.stdin)["number_of_nodes"])' 2>/dev/null || true)
  if [ "$current" = "$version" ] && [ "$nodes" = "3" ]; then
    printf 'performed full-restart upgrade\n' > submit.txt
    exit 0
  fi
  sleep 5
done

exit 1
