#!/usr/bin/env bash
set -euo pipefail

# Generated for review on 2026-06-26 from the live es-cluster Elasticsearch 7.17.9
# cluster in namespace elasticsearch.
#
# This script is intentionally guarded. It will not change Elasticsearch unless
# CONFIRM_ROLLBACK=yes is present in the environment.
#
# Read-only inspection summary:
# - Dynamic cluster settings were empty: persistent={} and transient={}.
# - ILM policies present were Elastic-managed built-in policies only.
# - Node attributes were Elastic built-ins only: xpack.installed=true and
#   transform.node=false. No custom node.attr values were observed.
# - The only writable live non-default index settings observed were on the two
#   managed hidden data stream backing indices below.
# - .geoip_databases is a reserved system index; direct settings access is
#   rejected by Elasticsearch and is intentionally not modified here.
#
# Static or creation-time index metadata such as index.uuid, index.creation_date,
# index.version.created, index.provided_name, and index.number_of_shards cannot be
# reset to defaults through the update-settings API. The hidden data stream
# identity is also left untouched here because removing it is a destructive
# data-stream recreation decision, not a settings rollback.

NS="${NS:-elasticsearch}"
CURL_POD="${CURL_POD:-curl-test}"
ES_URL="${ES_URL:-http://es-http.elasticsearch.svc:9200}"

ILM_HISTORY_INDEX=".ds-ilm-history-5-2026.06.26-000001"
DEPRECATION_INDEX=".ds-.logs-deprecation.elasticsearch-default-2026.06.26-000001"

cat <<SUMMARY
Rollback rehearsal script for ${ES_URL}

Targets generated from current live state:
  - ${ILM_HISTORY_INDEX}
  - ${DEPRECATION_INDEX}

No custom ILM policies, custom node attributes, or persistent/transient cluster
settings were observed at generation time. Common dynamic cluster routing
settings are still cleared below as a no-op-safe rollback guard.
SUMMARY

if [[ "${CONFIRM_ROLLBACK:-}" != "yes" ]]; then
  cat <<'GUARD'

Review guard active: no Elasticsearch changes were made.
To execute during the approved rollback window, run with:

  CONFIRM_ROLLBACK=yes ./rollback.sh

GUARD
  exit 0
fi

es_no_body() {
  local method="$1"
  local path="$2"
  kubectl -n "$NS" exec "$CURL_POD" -- \
    curl -fsS -X "$method" "${ES_URL}${path}"
}

es_json() {
  local method="$1"
  local path="$2"
  kubectl -n "$NS" exec -i "$CURL_POD" -- \
    curl -fsS -X "$method" "${ES_URL}${path}" \
      -H 'Content-Type: application/json' \
      --data-binary @-
}

echo "Clearing dynamic cluster routing overrides if any exist..."
es_json PUT "/_cluster/settings?flat_settings=true" <<'JSON'
{
  "persistent": {
    "cluster.routing.allocation.allow_rebalance": null,
    "cluster.routing.allocation.awareness.attributes": null,
    "cluster.routing.allocation.cluster_concurrent_rebalance": null,
    "cluster.routing.allocation.disk.threshold_enabled": null,
    "cluster.routing.allocation.enable": null,
    "cluster.routing.allocation.exclude._tier": null,
    "cluster.routing.allocation.include._tier": null,
    "cluster.routing.allocation.node_concurrent_incoming_recoveries": null,
    "cluster.routing.allocation.node_concurrent_outgoing_recoveries": null,
    "cluster.routing.allocation.node_concurrent_recoveries": null,
    "cluster.routing.allocation.node_initial_primaries_recoveries": null,
    "cluster.routing.allocation.require._tier": null,
    "cluster.routing.allocation.same_shard.host": null,
    "cluster.routing.allocation.total_shards_per_node": null,
    "cluster.routing.rebalance.enable": null
  },
  "transient": {
    "cluster.routing.allocation.allow_rebalance": null,
    "cluster.routing.allocation.awareness.attributes": null,
    "cluster.routing.allocation.cluster_concurrent_rebalance": null,
    "cluster.routing.allocation.disk.threshold_enabled": null,
    "cluster.routing.allocation.enable": null,
    "cluster.routing.allocation.exclude._tier": null,
    "cluster.routing.allocation.include._tier": null,
    "cluster.routing.allocation.node_concurrent_incoming_recoveries": null,
    "cluster.routing.allocation.node_concurrent_outgoing_recoveries": null,
    "cluster.routing.allocation.node_concurrent_recoveries": null,
    "cluster.routing.allocation.node_initial_primaries_recoveries": null,
    "cluster.routing.allocation.require._tier": null,
    "cluster.routing.allocation.same_shard.host": null,
    "cluster.routing.allocation.total_shards_per_node": null,
    "cluster.routing.rebalance.enable": null
  }
}
JSON

echo
echo "Removing ILM assignments from observed backing indices..."
es_no_body POST "/${ILM_HISTORY_INDEX}/_ilm/remove?expand_wildcards=all"
echo
es_no_body POST "/${DEPRECATION_INDEX}/_ilm/remove?expand_wildcards=all"
echo

echo "Resetting dynamic index settings on ${ILM_HISTORY_INDEX}..."
es_json PUT "/${ILM_HISTORY_INDEX}/_settings?expand_wildcards=all" <<'JSON'
{
  "index.auto_expand_replicas": null,
  "index.lifecycle.name": null,
  "index.routing.allocation.include._tier_preference": null
}
JSON

echo
echo "Resetting dynamic index settings on ${DEPRECATION_INDEX}..."
es_json PUT "/${DEPRECATION_INDEX}/_settings?expand_wildcards=all" <<'JSON'
{
  "index.auto_expand_replicas": null,
  "index.codec": null,
  "index.lifecycle.name": null,
  "index.query.default_field": null,
  "index.routing.allocation.include._tier_preference": null
}
JSON

echo
echo "Rollback settings calls completed. Review cluster health and shard allocation before ending the change window."
