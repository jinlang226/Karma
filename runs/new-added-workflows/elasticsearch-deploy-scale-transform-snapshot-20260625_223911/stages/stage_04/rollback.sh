#!/usr/bin/env bash
set -euo pipefail

# Generated for review from the live cluster state observed on 2026-06-25.
#
# Observed non-defaults:
# - app-data has index.number_of_shards=3. Elasticsearch 8.x defaults new
#   indices to 1 primary shard, but this setting is immutable in place.
# - app-data has index.routing.allocation.require.node_group=expansion.
# - es-data nodes have node.attr.node_group=expansion from ConfigMap
#   es-data-config.
#
# Observed defaults/no-ops:
# - Dynamic cluster settings were empty: {"persistent":{},"transient":{}}.
# - app-data is not ILM-managed. Existing ILM policies were Elasticsearch
#   stack-managed/default policies and are not changed here.
# - index.number_of_replicas=1 and index.routing.allocation.include._tier_preference
#   data_content match normal Elasticsearch 8 behavior for this cluster.

NAMESPACE="${NAMESPACE:-elasticsearch}"
RUNNER_POD="${RUNNER_POD:-curl-test}"
ES_URL="${ES_URL:-http://es-http.elasticsearch.svc:9200}"

if [[ "${CONFIRM_ROLLBACK:-}" != "yes" ]]; then
  cat >&2 <<'MSG'
Refusing to run: set CONFIRM_ROLLBACK=yes during the approved change window.

This script will reset the directly mutable index allocation setting on app-data.
Set RECREATE_APP_DATA_FOR_DEFAULT_SHARDS=yes to also recreate app-data with the
default primary shard count. Set REMOVE_NODE_GROUP_ATTR=yes to update es-data
node config and restart the es-data StatefulSet pods one at a time.
MSG
  exit 2
fi

es() {
  kubectl -n "$NAMESPACE" exec -i "$RUNNER_POD" -- curl -fsS "$@"
}

wait_green() {
  es "$ES_URL/_cluster/health?wait_for_status=green&timeout=120s" >/dev/null
}

count_docs() {
  es "$ES_URL/$1/_count" | sed -E 's/.*"count":([0-9]+).*/\1/'
}

put_app_data_mapping() {
  local index="$1"

  es -XPUT "$ES_URL/$index" -H 'Content-Type: application/json' -d @- <<'JSON'
{
  "mappings": {
    "properties": {
      "msg": {
        "type": "text",
        "fields": {
          "keyword": {
            "type": "keyword",
            "ignore_above": 256
          }
        }
      }
    }
  }
}
JSON
}

echo "Current dynamic cluster settings, for operator review:"
es "$ES_URL/_cluster/settings?flat_settings=true"
echo

echo "Clearing app-data index.routing.allocation.require.node_group..."
es -XPUT "$ES_URL/app-data/_settings" -H 'Content-Type: application/json' -d @- <<'JSON'
{
  "index": {
    "routing": {
      "allocation": {
        "require": {
          "node_group": null
        }
      }
    }
  }
}
JSON
echo
wait_green

if [[ "${RECREATE_APP_DATA_FOR_DEFAULT_SHARDS:-no}" == "yes" ]]; then
  tmp_index="${TMP_INDEX:-app-data-defaults-rollback-$(date +%Y%m%d%H%M%S)}"

  echo "Recreating app-data to reset immutable index.number_of_shards to the Elasticsearch default..."
  es -XPUT "$ES_URL/app-data/_settings" -H 'Content-Type: application/json' -d '{"index.blocks.write":true}'
  source_count="$(count_docs app-data)"

  put_app_data_mapping "$tmp_index"
  es -XPOST "$ES_URL/_reindex?wait_for_completion=true&refresh=true" \
    -H 'Content-Type: application/json' \
    -d "{\"source\":{\"index\":\"app-data\"},\"dest\":{\"index\":\"$tmp_index\"}}"
  echo

  tmp_count="$(count_docs "$tmp_index")"
  if [[ "$source_count" != "$tmp_count" ]]; then
    echo "Refusing to replace app-data: source count $source_count != temporary count $tmp_count" >&2
    exit 1
  fi

  es -XDELETE "$ES_URL/app-data"
  echo
  put_app_data_mapping app-data
  es -XPOST "$ES_URL/_reindex?wait_for_completion=true&refresh=true" \
    -H 'Content-Type: application/json' \
    -d "{\"source\":{\"index\":\"$tmp_index\"},\"dest\":{\"index\":\"app-data\"}}"
  echo

  final_count="$(count_docs app-data)"
  if [[ "$source_count" != "$final_count" ]]; then
    echo "Recreated app-data count $final_count does not match original count $source_count; leaving $tmp_index for recovery" >&2
    exit 1
  fi

  es -XDELETE "$ES_URL/$tmp_index"
  echo
  wait_green
else
  cat <<'MSG'
Skipping immutable shard-count rollback. app-data still requires a recreate to
move from 3 primary shards to the Elasticsearch default of 1. Re-run with
RECREATE_APP_DATA_FOR_DEFAULT_SHARDS=yes during a write-stopped window if that
rollback is approved.
MSG
fi

if [[ "${REMOVE_NODE_GROUP_ATTR:-no}" == "yes" ]]; then
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "$tmpdir"' EXIT

  echo "Removing node.attr.node_group from es-data-config..."
  kubectl -n "$NAMESPACE" get configmap es-data-config \
    -o jsonpath='{.data.elasticsearch\.yml}' > "$tmpdir/elasticsearch.yml.current"
  sed '/^node\.attr\.node_group: expansion$/d' \
    "$tmpdir/elasticsearch.yml.current" > "$tmpdir/elasticsearch.yml"

  if cmp -s "$tmpdir/elasticsearch.yml.current" "$tmpdir/elasticsearch.yml"; then
    echo "node.attr.node_group was not present in es-data-config; no ConfigMap update needed."
  else
    kubectl -n "$NAMESPACE" create configmap es-data-config \
      --from-file=elasticsearch.yml="$tmpdir/elasticsearch.yml" \
      --dry-run=client -o yaml | kubectl apply -f -

    for pod in $(kubectl -n "$NAMESPACE" get pods -l app=es-data \
      -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort); do
      echo "Restarting $pod to pick up es-data-config..."
      kubectl -n "$NAMESPACE" delete pod "$pod" --wait=true
      until kubectl -n "$NAMESPACE" get pod "$pod" >/dev/null 2>&1; do
        sleep 2
      done
      kubectl -n "$NAMESPACE" wait --for=condition=Ready "pod/$pod" --timeout=300s
      wait_green
    done
  fi
else
  cat <<'MSG'
Skipping node attribute rollback. Remove node.attr.node_group and restart
es-data pods by re-running with REMOVE_NODE_GROUP_ATTR=yes during an approved
maintenance window.
MSG
fi

echo "Rollback script completed."
