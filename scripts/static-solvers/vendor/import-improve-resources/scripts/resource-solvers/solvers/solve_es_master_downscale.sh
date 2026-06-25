#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
api="http://$service:9200"

kubectl -n "$ns" scale "statefulset/$prefix" --replicas=3
kubectl -n "$ns" wait --for=condition=ready pod -l "app=$prefix" --timeout=900s
kubectl -n "$ns" exec curl-test -- curl -fsS -XPUT "$api/_cluster/settings" \
  -H 'Content-Type: application/json' \
  -d '{"persistent":{"cluster.auto_shrink_voting_configuration":true}}' >/dev/null
kubectl -n "$ns" exec curl-test -- curl -fsS -XPUT "$api/_cluster/settings" \
  -H 'Content-Type: application/json' \
  -d "{\"transient\":{\"cluster.routing.allocation.require._name\":\"$prefix-0\"}}" \
  >/dev/null
kubectl -n "$ns" exec curl-test -- curl -fsS \
  "$api/_cluster/health?wait_for_status=yellow&wait_for_no_relocating_shards=true&wait_for_no_initializing_shards=true&timeout=120s" \
  >/dev/null
kubectl -n "$ns" exec curl-test -- curl -sS -XDELETE \
  "$api/_cluster/voting_config_exclusions?wait_for_removal=false" >/dev/null || true
kubectl -n "$ns" exec curl-test -- curl -fsS -XPOST \
  "$api/_cluster/voting_config_exclusions?node_names=$prefix-1,$prefix-2&timeout=60s" >/dev/null
kubectl -n "$ns" scale "statefulset/$prefix" --replicas=1
kubectl -n "$ns" wait --for=delete "pod/$prefix-2" --timeout=600s
kubectl -n "$ns" wait --for=delete "pod/$prefix-1" --timeout=600s
kubectl -n "$ns" exec curl-test -- curl -fsS -XDELETE \
  "$api/_cluster/voting_config_exclusions?wait_for_removal=true" >/dev/null
kubectl -n "$ns" exec curl-test -- curl -fsS -XPUT "$api/_cluster/settings" \
  -H 'Content-Type: application/json' \
  -d '{"transient":{"cluster.routing.allocation.require._name":null}}' >/dev/null
kubectl -n "$ns" exec curl-test -- curl -fsS \
  "$api/_cluster/health?wait_for_status=yellow&wait_for_nodes=1&timeout=60s" \
  >/dev/null
printf 'recovered voting configuration and safely downscaled\n' > submit.txt
