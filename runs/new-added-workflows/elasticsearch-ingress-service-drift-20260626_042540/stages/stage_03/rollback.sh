#!/usr/bin/env bash
set -euo pipefail

# Prepared rollback rehearsal for Elasticsearch configuration drift.
# Generated from live read-only inspection on 2026-06-26.
#
# Current inspection summary:
# - Kubernetes namespace: elasticsearch
# - HTTP service: http://es-http.elasticsearch.svc:9200
# - Dynamic cluster settings:
#     /_cluster/settings?flat_settings=true returned persistent={} and transient={}.
# - Indices:
#     Only hidden system index .geoip_databases was present.
#     Cluster metadata showed:
#       index.routing.allocation.include._tier_preference=data_content
#       index.auto_expand_replicas=0-1
#       index.number_of_replicas=1
#       index.number_of_shards=1
#     Static/index identity settings such as number_of_shards, uuid,
#     creation_date, provided_name, and version.created are not mutable and are
#     intentionally not changed here.
# - ILM policies:
#     Returned policies were Elastic managed defaults with _meta.managed=true;
#     no custom ILM policy rollback command is included.
# - Node attributes:
#     Nodes reported xpack.installed=true and transform.node=false. These are
#     node/static attributes and are not changed through Elasticsearch settings
#     APIs by this rollback script.
#
# This script is intentionally guarded. Do not run during normal operations.
# To execute during an approved change window:
#   CONFIRM_ROLLBACK=YES ./rollback.sh

NS="${NS:-elasticsearch}"
CURL_POD="${CURL_POD:-curl-test}"
ES_URL="${ES_URL:-http://es-http.elasticsearch.svc:9200}"

if [[ "${CONFIRM_ROLLBACK:-}" != "YES" ]]; then
  cat >&2 <<'MSG'
This rollback script is prepared for review only and did not run when stored.
It would reset mutable Elasticsearch settings back to defaults.

Run only during an approved change window, for example:
  CONFIRM_ROLLBACK=YES ./rollback.sh
MSG
  exit 64
fi

es_curl() {
  kubectl -n "${NS}" exec "${CURL_POD}" -- curl -sS "$@"
}

echo "Capturing current settings before rollback..."
es_curl "${ES_URL}/_cluster/settings?flat_settings=true"
es_curl "${ES_URL}/_cluster/state/metadata?filter_path=metadata.indices.*.settings.index.*"

echo "Resetting known mutable dynamic cluster routing settings to defaults..."
es_curl -X PUT "${ES_URL}/_cluster/settings" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<'JSON'
{
  "persistent": {
    "cluster.routing.allocation.enable": null,
    "cluster.routing.allocation.node_concurrent_incoming_recoveries": null,
    "cluster.routing.allocation.node_concurrent_outgoing_recoveries": null,
    "cluster.routing.allocation.node_concurrent_recoveries": null,
    "cluster.routing.allocation.node_initial_primaries_recoveries": null,
    "cluster.routing.allocation.same_shard.host": null,
    "cluster.routing.allocation.balance.shard": null,
    "cluster.routing.allocation.balance.index": null,
    "cluster.routing.allocation.balance.threshold": null,
    "cluster.routing.allocation.awareness.attributes": null,
    "cluster.routing.allocation.exclude._name": null,
    "cluster.routing.allocation.exclude._host": null,
    "cluster.routing.allocation.exclude._ip": null,
    "cluster.routing.allocation.include._name": null,
    "cluster.routing.allocation.include._host": null,
    "cluster.routing.allocation.include._ip": null,
    "cluster.routing.allocation.require._name": null,
    "cluster.routing.allocation.require._host": null,
    "cluster.routing.allocation.require._ip": null,
    "cluster.routing.rebalance.enable": null,
    "cluster.routing.allocation.cluster_concurrent_rebalance": null
  },
  "transient": {
    "cluster.routing.allocation.enable": null,
    "cluster.routing.allocation.node_concurrent_incoming_recoveries": null,
    "cluster.routing.allocation.node_concurrent_outgoing_recoveries": null,
    "cluster.routing.allocation.node_concurrent_recoveries": null,
    "cluster.routing.allocation.node_initial_primaries_recoveries": null,
    "cluster.routing.allocation.same_shard.host": null,
    "cluster.routing.allocation.balance.shard": null,
    "cluster.routing.allocation.balance.index": null,
    "cluster.routing.allocation.balance.threshold": null,
    "cluster.routing.allocation.awareness.attributes": null,
    "cluster.routing.allocation.exclude._name": null,
    "cluster.routing.allocation.exclude._host": null,
    "cluster.routing.allocation.exclude._ip": null,
    "cluster.routing.allocation.include._name": null,
    "cluster.routing.allocation.include._host": null,
    "cluster.routing.allocation.include._ip": null,
    "cluster.routing.allocation.require._name": null,
    "cluster.routing.allocation.require._host": null,
    "cluster.routing.allocation.require._ip": null,
    "cluster.routing.rebalance.enable": null,
    "cluster.routing.allocation.cluster_concurrent_rebalance": null
  }
}
JSON

echo "Resetting mutable explicit .geoip_databases index settings to defaults..."
es_curl -X PUT "${ES_URL}/.geoip_databases/_settings?expand_wildcards=all" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<'JSON'
{
  "index.routing.allocation.include._tier_preference": null,
  "index.auto_expand_replicas": null,
  "index.number_of_replicas": null
}
JSON

echo "Rollback commands completed. Review cluster health and allocation state."
es_curl "${ES_URL}/_cluster/health?pretty"
es_curl "${ES_URL}/_cat/indices?v&expand_wildcards=all"
