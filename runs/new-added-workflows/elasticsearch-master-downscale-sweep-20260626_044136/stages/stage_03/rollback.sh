#!/usr/bin/env sh
set -eu

# Rollback rehearsal script prepared from the live cluster state observed on
# 2026-06-26.
#
# Live non-default / explicitly-set state observed:
# - Cluster setting: persistent cluster.auto_shrink_voting_configuration=true
#   (same value as the Elasticsearch 7.17 default, but explicitly persisted).
# - Index app-data:
#   - index.number_of_shards=3, while the Elasticsearch default is 1.
#   - index.number_of_replicas=0, while the Elasticsearch default is 1.
#   - index.routing.allocation.include._tier_preference=data_content is present.
# - ILM policies: only Elasticsearch-managed built-in policies were found.
#   No custom ILM policy deletion is included.
# - Node attributes: xpack.installed=true and transform.node=false were observed.
#   These are node/runtime metadata and are not changed through index or cluster
#   settings APIs.
#
# Safety: this script is intentionally inert unless CONFIRM_ROLLBACK=yes is set.
# Recreating app-data to change its primary shard count is destructive and
# requires a write outage for that index.

ES_URL="${ES_URL:-http://es-http.elasticsearch.svc:9200}"
CONFIRM_ROLLBACK="${CONFIRM_ROLLBACK:-no}"
RECREATE_STATIC_INDEXES="${RECREATE_STATIC_INDEXES:-no}"
WORK_INDEX="${WORK_INDEX:-app-data-rollback-defaults}"

request() {
  method="$1"
  path="$2"
  body="${3:-}"

  if [ "$#" -eq 3 ]; then
    printf '%s' "$body" | curl -fsS -X "$method" \
      -H 'Content-Type: application/json' \
      --data-binary @- \
      "${ES_URL}${path}"
  else
    curl -fsS -X "$method" "${ES_URL}${path}"
  fi
  printf '\n'
}

if [ "$CONFIRM_ROLLBACK" != "yes" ]; then
  cat >&2 <<'MSG'
Refusing to run rollback actions.
Set CONFIRM_ROLLBACK=yes during the approved change window after review.

Set RECREATE_STATIC_INDEXES=yes as well only if app-data should be recreated
with Elasticsearch's default primary shard count. That path deletes and
recreates app-data after reindexing through a temporary index.
MSG
  exit 2
fi

printf 'Clearing explicitly persisted cluster setting back to default...\n'
request PUT '/_cluster/settings' \
  '{"persistent":{"cluster.auto_shrink_voting_configuration":null},"transient":{}}'

if [ "$RECREATE_STATIC_INDEXES" != "yes" ]; then
  printf 'Resetting dynamic app-data settings back to defaults...\n'
  request PUT '/app-data/_settings' \
    '{"index.number_of_replicas":null,"index.routing.allocation.include._tier_preference":null}'

  cat <<'MSG'
Dynamic rollback requests have been submitted.
app-data still has 3 primary shards because index.number_of_shards is static.
Run again with RECREATE_STATIC_INDEXES=yes during a write outage to recreate
app-data with the default primary shard count.
MSG
  exit 0
fi

cat <<'MSG'
Recreating app-data with default index settings.
This requires writers to app-data to be stopped for the duration of the script.
MSG

APP_DATA_MAPPING='{"mappings":{"properties":{"msg":{"type":"text","fields":{"keyword":{"type":"keyword","ignore_above":256}}}}}}'

printf 'Removing any previous temporary rollback index...\n'
request DELETE "/${WORK_INDEX}?ignore_unavailable=true"

printf 'Creating temporary index with default settings and the observed mapping...\n'
request PUT "/${WORK_INDEX}" "$APP_DATA_MAPPING"

printf 'Blocking writes to app-data before reindexing...\n'
request PUT '/app-data/_block/write' '{}'

printf 'Copying app-data into temporary index...\n'
request POST '/_reindex?wait_for_completion=true&refresh=true' \
  "$(printf '{"source":{"index":"app-data"},"dest":{"index":"%s"}}' "$WORK_INDEX")"

printf 'Deleting original app-data index...\n'
request DELETE '/app-data'

printf 'Recreating app-data with default settings and the observed mapping...\n'
request PUT '/app-data' "$APP_DATA_MAPPING"

printf 'Copying data back into app-data...\n'
request POST '/_reindex?wait_for_completion=true&refresh=true' \
  "$(printf '{"source":{"index":"%s"},"dest":{"index":"app-data"}}' "$WORK_INDEX")"

printf 'Removing temporary rollback index...\n'
request DELETE "/${WORK_INDEX}"

printf 'Rollback requests have completed. Review cluster and index health before reopening writes.\n'
