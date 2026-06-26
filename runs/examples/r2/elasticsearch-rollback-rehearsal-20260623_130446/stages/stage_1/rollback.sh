#!/usr/bin/env bash
# Elasticsearch rollback script — reverts non-default settings to ES defaults.
#
# Inspected state (2026-06-23):
#   Cluster:   es-cluster, 3 nodes (es-cluster-0/1/2), ES 8.11.1
#   Namespace: elasticsearch
#   Endpoint:  http://es-http.elasticsearch.svc:9200
#
# Non-default settings found:
#   INDEX audit-index:
#     index.number_of_shards                        = 3   (ES default: 1)  -- STATIC
#     index.routing.allocation.require.tier         = hot (ES default: unset)
#     index.routing.allocation.include._tier_preference = data_content (ES default: unset)
#   NODE ATTRIBUTES (all nodes):
#     node.attr.tier = hot   -- set in elasticsearch.yml / StatefulSet; NOT revertible via API
#   CLUSTER SETTINGS (persistent + transient):
#     Both empty at inspection time — nothing to revert via /_cluster/settings.
#
# EXECUTION NOTE: run this from inside the cluster, e.g.:
#   kubectl -n elasticsearch exec curl-test -- bash /path/to/rollback.sh
# or pipe it:
#   kubectl -n elasticsearch exec -i curl-test -- bash -s < rollback.sh
#
# DO NOT EXECUTE until the change window is approved.

set -euo pipefail

ES_URL="${ES_URL:-http://es-http.elasticsearch.svc:9200}"
PASS=0
FAIL=0

die() { echo "[ERROR] $*" >&2; exit 1; }

check() {
  # check <label> <curl-response-body>
  local label="$1" body="$2"
  if echo "$body" | grep -q '"acknowledged":true'; then
    echo "[OK]   $label"
    PASS=$((PASS + 1))
  else
    echo "[FAIL] $label -- response: $body"
    FAIL=$((FAIL + 1))
  fi
}

echo "========================================================"
echo " Elasticsearch Rollback — $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo " Endpoint: ${ES_URL}"
echo "========================================================"
echo ""

# ── 1. Verify cluster is reachable ─────────────────────────────────────────
echo "--- [1] Verify cluster reachable ---"
curl -sf "${ES_URL}/_cluster/health?timeout=10s" -o /dev/null \
  || die "Cluster unreachable at ${ES_URL}"
echo "[OK]   Cluster reachable"
echo ""

# ── 2. Cluster settings ─────────────────────────────────────────────────────
# At inspection time both persistent and transient were empty {}.
# Nothing to revert. This block is present for auditability; if a non-default
# cluster setting is found it should be added here.
echo "--- [2] Cluster settings ---"
CLUSTER_SETTINGS=$(curl -sf "${ES_URL}/_cluster/settings?flat_settings=true")
PERSISTENT=$(echo "$CLUSTER_SETTINGS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('persistent',{})))")
TRANSIENT=$(echo  "$CLUSTER_SETTINGS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('transient',{})))")
echo "[INFO] persistent settings count: ${PERSISTENT}, transient settings count: ${TRANSIENT}"
if [ "${PERSISTENT}" -eq 0 ] && [ "${TRANSIENT}" -eq 0 ]; then
  echo "[SKIP] No API-level cluster settings to revert."
else
  echo "[WARN] Unexpected cluster settings found — review manually before proceeding."
fi
echo ""

# ── 3. Index: audit-index — dynamic routing settings ───────────────────────
# These two settings were explicitly configured (non-default) and can be
# cleared by setting them to null.
echo "--- [3] audit-index: clear routing.allocation.require.tier ---"
RESP=$(curl -sf -X PUT "${ES_URL}/audit-index/_settings" \
  -H 'Content-Type: application/json' \
  -d '{
    "index": {
      "routing": {
        "allocation": {
          "require": {
            "tier": null
          }
        }
      }
    }
  }')
check "audit-index routing.allocation.require.tier -> null" "$RESP"
echo ""

echo "--- [4] audit-index: clear routing.allocation.include._tier_preference ---"
RESP=$(curl -sf -X PUT "${ES_URL}/audit-index/_settings" \
  -H 'Content-Type: application/json' \
  -d '{
    "index": {
      "routing": {
        "allocation": {
          "include": {
            "_tier_preference": null
          }
        }
      }
    }
  }')
check "audit-index routing.allocation.include._tier_preference -> null" "$RESP"
echo ""

# ── 4. Index: audit-index — static shard count ─────────────────────────────
# number_of_shards=3 was set at creation time (ES default: 1).
# Static settings cannot be changed on a live index via the settings API.
# Reverting requires: snapshot → delete → recreate with shards=1 → restore.
echo "--- [5] audit-index: number_of_shards (STATIC — requires reindex) ---"
echo "[WARN] index.number_of_shards=3 cannot be changed on a live index."
echo "       To revert to default (1 shard), perform the following steps:"
echo "         a) Snapshot audit-index to a repository."
echo "         b) Delete audit-index."
echo "         c) Recreate audit-index with number_of_shards=1 (or omit to use the default)."
echo "         d) Restore documents from the snapshot."
echo "       Do NOT proceed without verifying the snapshot is healthy."
echo ""

# ── 5. Node attributes: tier=hot ────────────────────────────────────────────
# node.attr.tier=hot is set on all three nodes via elasticsearch.yml
# (injected through the StatefulSet pod template, not via the ES API).
# This cannot be reverted by an API call; it requires:
#   1. Removing/updating the node.attr.tier line in the ConfigMap / env var
#      backing the StatefulSet.
#   2. Rolling restart of the StatefulSet so pods pick up the new config.
echo "--- [6] Node attributes: tier=hot (STATIC — requires StatefulSet change) ---"
echo "[WARN] node.attr.tier=hot is set in elasticsearch.yml on all nodes."
echo "       Revert by:"
echo "         a) Edit the StatefulSet / ConfigMap that sets node.attr.tier."
echo "         b) Remove (or change) the tier attribute."
echo "         c) kubectl rollout restart statefulset/es-cluster -n elasticsearch"
echo "         d) Wait for rolling restart: kubectl rollout status statefulset/es-cluster -n elasticsearch"
echo ""

# ── Summary ─────────────────────────────────────────────────────────────────
echo "========================================================"
echo " Summary"
echo "========================================================"
echo " API changes:   PASS=${PASS}  FAIL=${FAIL}"
echo " Manual steps:  2 (number_of_shards reindex, node attribute rolling restart)"
if [ "${FAIL}" -gt 0 ]; then
  echo ""
  echo "[ERROR] ${FAIL} API step(s) failed — review output above before declaring rollback complete."
  exit 1
fi
echo ""
echo " Rollback complete (API steps). Verify with:"
echo "   curl -s '${ES_URL}/audit-index/_settings?flat_settings=true&filter_path=**.routing'"
echo "   curl -s '${ES_URL}/_cluster/settings?flat_settings=true'"
