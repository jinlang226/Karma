#!/usr/bin/env bash
#
# rollback.sh — REHEARSAL ONLY. DO NOT RUN against the live cluster yet.
#
# Purpose: revert the non-default Elasticsearch configuration discovered in the
# `elasticsearch` namespace back to Elasticsearch defaults. Reviewed and executed
# only during an approved change window.
#
# Discovered non-default state (2026-06-18):
#   - Index `audit-index`:
#       index.routing.allocation.require.tier            = hot   (custom)
#       index.routing.allocation.include._tier_preference = data_content
#       index.number_of_replicas                          = 1
#       index.number_of_shards                            = 3     (immutable — see note)
#   - Cluster settings (persistent/transient): none set.
#   - ILM policies: only built-in managed policies present.
#   - Node attribute `tier=hot`: static node config (elasticsearch.yml /
#     node.attr.tier), NOT revertible via the REST API — requires editing the
#     StatefulSet pod spec / config and a rolling restart. Handled separately.
#
set -euo pipefail

ES="${ES_URL:-http://es-http.elasticsearch.svc:9200}"
INDEX="${TARGET_INDEX:-audit-index}"

curl_es() { curl -sS -H 'Content-Type: application/json' "$@"; }

echo ">> Current cluster settings (for reference):"
curl_es "${ES}/_cluster/settings?flat_settings=true"
echo

# ---------------------------------------------------------------------------
# 1) Reset any persistent/transient cluster settings to defaults.
#    None are currently set; included for completeness / idempotency.
# ---------------------------------------------------------------------------
echo ">> Clearing transient & persistent cluster shard-allocation overrides..."
curl_es -X PUT "${ES}/_cluster/settings" -d '{
  "transient": {
    "cluster.routing.allocation.*": null
  },
  "persistent": {
    "cluster.routing.allocation.*": null
  }
}'
echo

# ---------------------------------------------------------------------------
# 2) Revert dynamic index settings on audit-index to defaults.
#    - Remove the custom tier-based shard allocation routing.
#    - Reset number_of_replicas to the Elasticsearch default (1).
# ---------------------------------------------------------------------------
echo ">> Reverting dynamic settings on index '${INDEX}'..."
curl_es -X PUT "${ES}/${INDEX}/_settings" -d '{
  "index": {
    "routing.allocation.require.tier": null,
    "routing.allocation.include._tier_preference": null,
    "number_of_replicas": 1
  }
}'
echo

# ---------------------------------------------------------------------------
# 3) number_of_shards is IMMUTABLE on an existing index.
#    To return to the default (1 shard) you must reindex into a fresh index.
#    Left as a manual, reviewed step — uncomment only if intended.
# ---------------------------------------------------------------------------
# echo ">> Reindexing '${INDEX}' to a single-shard default index..."
# curl_es -X PUT "${ES}/${INDEX}-default" -d '{"settings":{"index":{"number_of_shards":1,"number_of_replicas":1}}}'
# curl_es -X POST "${ES}/_reindex" -d "{\"source\":{\"index\":\"${INDEX}\"},\"dest\":{\"index\":\"${INDEX}-default\"}}"
# curl_es -X DELETE "${ES}/${INDEX}"
# curl_es -X POST "${ES}/_aliases" -d "{\"actions\":[{\"add\":{\"index\":\"${INDEX}-default\",\"alias\":\"${INDEX}\"}}]}"

echo ">> Rollback complete. Verify with:"
echo "   curl -s ${ES}/${INDEX}/_settings?flat_settings=true"
echo "   curl -s ${ES}/_cluster/settings?flat_settings=true"
