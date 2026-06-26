#!/usr/bin/env bash
set -euo pipefail

# Rollback rehearsal generated from the live cluster on 2026-06-25.
# Do not run during audit. This script is intended for review and execution
# only in an approved change window.

NS="${NS:-elasticsearch}"
ES_URL="${ES_URL:-http://es-http.elasticsearch.svc:9200}"
CURL_POD="${CURL_POD:-curl-test}"
KUBECTL="${KUBECTL:-kubectl}"

es_curl() {
  "${KUBECTL}" -n "${NS}" exec "${CURL_POD}" -- curl -fsS "$@"
}

wait_for_green_or_yellow() {
  es_curl "${ES_URL}/_cluster/health?wait_for_status=yellow&wait_for_no_relocating_shards=true&timeout=120s"
}

echo "Preflight: current cluster health"
wait_for_green_or_yellow

echo "Cluster settings rollback: no persistent or transient cluster settings were set at rehearsal time."

echo "Index allocation rollback: clear custom app-data node_pool requirement."
es_curl -X PUT "${ES_URL}/app-data/_settings" \
  -H 'Content-Type: application/json' \
  -d '{"index":{"routing":{"allocation":{"require":{"node_pool":null}}}}}'
wait_for_green_or_yellow

cat <<'NOTE'
app-data currently has index.number_of_shards=3. Elasticsearch's default is 1,
and shard count is immutable. The following steps recreate app-data with
default shard settings while preserving the current mapping and documents.
Review application write coordination before executing these steps.
NOTE

echo "Immutable index setting rollback: recreate app-data with default shard count."
es_curl -X PUT "${ES_URL}/app-data/_settings" \
  -H 'Content-Type: application/json' \
  -d '{"index":{"blocks":{"write":true}}}'

es_curl -X PUT "${ES_URL}/app-data-rollback-defaults" \
  -H 'Content-Type: application/json' \
  -d '{"mappings":{"properties":{"msg":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}}}}'

es_curl -X POST "${ES_URL}/_reindex?wait_for_completion=true&refresh=true" \
  -H 'Content-Type: application/json' \
  -d '{"source":{"index":"app-data"},"dest":{"index":"app-data-rollback-defaults"}}'

es_curl -X DELETE "${ES_URL}/app-data"

es_curl -X PUT "${ES_URL}/app-data" \
  -H 'Content-Type: application/json' \
  -d '{"mappings":{"properties":{"msg":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}}}}'

es_curl -X POST "${ES_URL}/_reindex?wait_for_completion=true&refresh=true" \
  -H 'Content-Type: application/json' \
  -d '{"source":{"index":"app-data-rollback-defaults"},"dest":{"index":"app-data"}}'

es_curl -X DELETE "${ES_URL}/app-data-rollback-defaults"
wait_for_green_or_yellow

echo "ILM rollback: app-data was unmanaged by ILM and no custom SLM policies existed at rehearsal time."
echo "Built-in managed ILM policies are intentionally left untouched."

cat <<'NOTE'
Node attribute rollback: es-extra nodes carry static node.attr.node_pool=expansion
from ConfigMap es-extra-config. Remove that static line and restart only es-extra
so the node attribute disappears from live node settings.
NOTE

tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

"${KUBECTL}" -n "${NS}" get configmap es-extra-config \
  -o jsonpath='{.data.elasticsearch\.yml}' > "${tmpdir}/elasticsearch.yml"

sed '/^node\.attr\.node_pool:[[:space:]]*expansion[[:space:]]*$/d' \
  "${tmpdir}/elasticsearch.yml" > "${tmpdir}/elasticsearch.rollback.yml"

"${KUBECTL}" -n "${NS}" create configmap es-extra-config \
  --from-file=elasticsearch.yml="${tmpdir}/elasticsearch.rollback.yml" \
  --dry-run=client -o yaml | "${KUBECTL}" apply -f -

"${KUBECTL}" -n "${NS}" rollout restart statefulset/es-extra
"${KUBECTL}" -n "${NS}" rollout status statefulset/es-extra --timeout=180s
wait_for_green_or_yellow

echo "Post-rollback verification"
es_curl "${ES_URL}/app-data/_settings?flat_settings=true"
es_curl "${ES_URL}/_nodes?filter_path=nodes.*.name,nodes.*.settings.node.attr,nodes.*.roles"
es_curl "${ES_URL}/_cluster/settings?flat_settings=true"
