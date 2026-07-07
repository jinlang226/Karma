#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
index="${BENCH_PARAM_INDEX_NAME:-app-data}"
api="http://$service:9200"

kubectl -n "$ns" exec curl-test -- curl -fsS -XPUT "$api/$index/_settings" \
  -H 'Content-Type: application/json' \
  -d "{\"index\":{\"number_of_replicas\":0,\"routing\":{\"allocation\":{\"include\":{\"_name\":\"$prefix-0\"}}}}}" >/dev/null
for attempt in $(seq 1 90); do
  nodes=$(kubectl -n "$ns" exec curl-test -- curl -sS "$api/_cat/shards/$index?format=json" | \
    python3 -c 'import json,sys; print(" ".join(sorted({x.get("node","") for x in json.load(sys.stdin) if x.get("node")})))')
  [ "$nodes" = "$prefix-0" ] && break
  sleep 3
done
[ "$nodes" = "$prefix-0" ]
kubectl -n "$ns" exec curl-test -- curl -fsS -XPOST \
  "$api/_cluster/voting_config_exclusions?node_names=$prefix-1,$prefix-2&timeout=60s" >/dev/null
kubectl -n "$ns" scale "statefulset/$prefix" --replicas=1
kubectl -n "$ns" wait --for=delete "pod/$prefix-2" --timeout=600s
kubectl -n "$ns" wait --for=delete "pod/$prefix-1" --timeout=600s
kubectl -n "$ns" exec curl-test -- curl -fsS -XDELETE \
  "$api/_cluster/voting_config_exclusions?wait_for_removal=true" >/dev/null
kubectl -n "$ns" delete "pvc/data-$prefix-1" "pvc/data-$prefix-2" \
  --ignore-not-found=true --wait=true
printf 'migrated shards, downscaled, and removed orphan PVCs\n' > submit.txt
