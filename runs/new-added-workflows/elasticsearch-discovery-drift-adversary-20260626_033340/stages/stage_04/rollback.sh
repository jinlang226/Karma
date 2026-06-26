#!/bin/sh
set -eu

# Prepared rollback rehearsal for the Elasticsearch cluster in namespace
# elasticsearch. This script is intentionally guarded so a review or accidental
# invocation cannot change the live cluster before the approved change window.
#
# Observed live state at preparation time:
# - Elasticsearch 8.11.1
# - Cluster settings: persistent={} and transient={}
# - ILM policies: built-in managed policies only; no custom policies to remove
# - Node attributes: built-in xpack/ml/transform attributes only
# - Index app-data has explicit index.routing.allocation.include._tier_preference=data_content

ES_URL="${ES_URL:-http://es-http.elasticsearch.svc:9200}"

if [ "${CONFIRM_ROLLBACK:-}" != "yes" ]; then
  cat >&2 <<'MSG'
Refusing to apply rollback without CONFIRM_ROLLBACK=yes.
This ConfigMap is a rollback rehearsal artifact for review before the change window.

To execute during an approved window:
  CONFIRM_ROLLBACK=yes ./rollback.sh
MSG
  exit 2
fi

echo "Resetting app-data allocation tier preference to the Elasticsearch default (unset)."
curl -fsS -X PUT -H 'Content-Type: application/json' \
  "${ES_URL}/app-data/_settings" \
  --data-binary @- <<'JSON'
{
  "settings": {
    "index.routing.allocation.include._tier_preference": null
  }
}
JSON
echo

echo "No cluster persistent/transient settings were observed; no cluster setting rollback is required."
echo "No custom ILM policies were observed; no ILM rollback is required."
echo "No custom node attributes were observed; no node configuration rollback is required."

echo "Post-rollback app-data settings:"
curl -fsS "${ES_URL}/app-data/_settings?flat_settings=true"
echo
