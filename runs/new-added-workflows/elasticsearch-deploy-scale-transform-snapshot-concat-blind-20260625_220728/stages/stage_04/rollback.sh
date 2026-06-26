#!/usr/bin/env bash
set -euo pipefail

# Rollback rehearsal prepared from the live elasticsearch namespace state on 2026-06-25.
# This script is intentionally guarded because it changes Elasticsearch and Kubernetes state.
#
# Observed live state:
# - Cluster persistent settings: {}
# - Cluster transient settings: {}
# - Index app-data has routing requirement: index.routing.allocation.require.node_group=expansion
# - Index app-data also has index.routing.allocation.include._tier_preference=data_content, which is the
#   Elasticsearch content-tier default and is not reset here.
# - app-data has index.number_of_shards=3; primary shard count is immutable in place and cannot be
#   reverted to the default by the settings API.
# - ILM policies observed were Elasticsearch managed/built-in policies only; no custom ILM rollback is included.
# - Nodes es-data-0 and es-data-1 have node.attr.node_group=expansion from ConfigMap/es-data-config.

NAMESPACE="${NAMESPACE:-elasticsearch}"
ES_URL="${ES_URL:-http://es-http.elasticsearch.svc:9200}"
CURL_POD="${CURL_POD:-curl-test}"

if [ "${CONFIRM_ROLLBACK:-}" != "yes" ]; then
  echo "Refusing to run rollback without CONFIRM_ROLLBACK=yes" >&2
  echo "Example: CONFIRM_ROLLBACK=yes $0" >&2
  exit 1
fi

es_curl() {
  kubectl -n "$NAMESPACE" exec "$CURL_POD" -- curl -fsS "$@"
}

echo "Removing app-data node_group allocation requirement..."
es_curl -X PUT "$ES_URL/app-data/_settings" \
  -H 'Content-Type: application/json' \
  -d '{"index.routing.allocation.require.node_group": null}'

echo "Waiting for cluster health after app-data routing reset..."
es_curl "$ES_URL/_cluster/health?wait_for_status=yellow&timeout=120s"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

cat > "$tmpdir/elasticsearch.yml" <<'YAML'
cluster.name: es-cluster
node.name: ${POD_NAME}
node.roles: [ data, ingest ]
network.host: 0.0.0.0
discovery.seed_hosts:
  - es-cluster-0.es-cluster.elasticsearch.svc.cluster.local
  - es-cluster-1.es-cluster.elasticsearch.svc.cluster.local
  - es-cluster-2.es-cluster.elasticsearch.svc.cluster.local
node.store.allow_mmap: false
xpack.security.enabled: false
xpack.security.http.ssl.enabled: false
xpack.security.transport.ssl.enabled: false
s3.client.default.endpoint: minio.elasticsearch.svc.cluster.local:9000
s3.client.default.protocol: http
s3.client.default.path_style_access: true
YAML

echo "Patching es-data-config to remove node.attr.node_group..."
kubectl -n "$NAMESPACE" create configmap es-data-config \
  --from-file=elasticsearch.yml="$tmpdir/elasticsearch.yml" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Restarting es-data pods so the mounted elasticsearch.yml change takes effect..."
kubectl -n "$NAMESPACE" rollout restart statefulset/es-data
kubectl -n "$NAMESPACE" rollout status statefulset/es-data --timeout=300s

echo "Final cluster health:"
es_curl "$ES_URL/_cluster/health?pretty"
