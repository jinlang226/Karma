#!/usr/bin/env bash
# Revert the mongodb replica set from its current non-default configuration
# back to MongoDB 6.0 defaults.
#
# Non-default settings being reverted:
#   systemLog.verbosity                          1      → 0      (MongoDB default)
#   operationProfiling.mode                      slowOp → off    (MongoDB default)
#   operationProfiling.slowOpThresholdMs         200    → 100    (MongoDB default)
#   storage.wiredTiger.engineConfig.journalCompressor  zlib → snappy  (WiredTiger default)
#
# Approach:
#   Step 1 – Patch the mongod-config ConfigMap so newly-started pods use defaults.
#   Step 2 – Push the runtime-settable changes (verbosity, profiling) to each
#            running replica member immediately via mongosh, so they take effect
#            without waiting for a pod restart.
#   Step 3 – Rolling restart of the StatefulSet so all pods reload the config
#            file and pick up the journalCompressor change (not runtime-settable).
#
# DO NOT execute this script while the cluster still depends on these settings.

set -euo pipefail

NAMESPACE="mongodb"
STATEFULSET="mongodb-replica"
CONFIGMAP="mongod-config"
SECRET="admin-user-password"
AUTH_DB="admin"
ADMIN_USER="admin-user"

# ---------------------------------------------------------------------------
# Retrieve admin credentials from the existing Secret.
# ---------------------------------------------------------------------------
ADMIN_PASSWORD=$(kubectl -n "$NAMESPACE" get secret "$SECRET" \
  -o jsonpath='{.data.password}' | base64 -d)

# ---------------------------------------------------------------------------
# Step 1: Patch mongod-config ConfigMap.
#   Removes: systemLog.verbosity, operationProfiling block.
#   Reverts: journalCompressor zlib → snappy.
#   Retains: all structural/required settings (dbPath, net, replication, security).
# ---------------------------------------------------------------------------
echo "==> Step 1: Patching ConfigMap '${CONFIGMAP}' to revert to defaults..."

TMPCONF=$(mktemp /tmp/mongod-default.conf.XXXXXX)
cat > "$TMPCONF" <<'EOF'
storage:
  dbPath: /data/db
  wiredTiger:
    engineConfig:
      journalCompressor: snappy
net:
  bindIpAll: true
replication:
  replSetName: mongodb-replica
security:
  authorization: enabled
  keyFile: /etc/mongo-keyfile/keyfile
EOF

kubectl -n "$NAMESPACE" create configmap "$CONFIGMAP" \
  --from-file=mongod.conf="$TMPCONF" \
  --dry-run=client -o yaml | kubectl apply -f -

rm -f "$TMPCONF"
echo "    ConfigMap '${CONFIGMAP}' updated."

# ---------------------------------------------------------------------------
# Step 2: Apply runtime-settable resets to every live replica member.
#   - logComponentVerbosity → 0 (was 1)
#   - profilingLevel         → 0 / off, slowms → 100 ms (was slowOp / 200 ms)
# These take effect immediately without a pod restart.
# ---------------------------------------------------------------------------
echo "==> Step 2: Resetting runtime parameters on each pod..."

PODS=$(kubectl -n "$NAMESPACE" get pods -l "app=${STATEFULSET}" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')

for pod in $PODS; do
  echo "    -> ${pod}"
  kubectl -n "$NAMESPACE" exec "$pod" -- mongosh --quiet \
    -u "$ADMIN_USER" -p "$ADMIN_PASSWORD" \
    --authenticationDatabase "$AUTH_DB" \
    --eval '
      db.adminCommand({ setParameter: 1, logComponentVerbosity: { verbosity: 0 } });
      db.setProfilingLevel(0, { slowms: 100 });
      print("  verbosity=0  profiling=off  slowms=100");
    '
done

# ---------------------------------------------------------------------------
# Step 3: Rolling restart so every pod reloads the updated config file.
# Required for the journalCompressor change; WiredTiger reads it only at
# startup. The StatefulSet rolling update strategy restarts one pod at a
# time, waiting for it to be Ready before proceeding to the next.
# ---------------------------------------------------------------------------
echo "==> Step 3: Rolling restart of StatefulSet '${STATEFULSET}'..."
kubectl -n "$NAMESPACE" rollout restart statefulset/"$STATEFULSET"
kubectl -n "$NAMESPACE" rollout status statefulset/"$STATEFULSET" --timeout=300s

echo ""
echo "==> Rollback complete."
echo ""
echo "Verify with:"
echo ""
echo "  # Log verbosity (expect: verbosity: 0)"
echo "  kubectl -n ${NAMESPACE} exec mongodb-replica-0 -- \\"
echo "    mongosh --quiet -u ${ADMIN_USER} -p '<password>' \\"
echo "    --authenticationDatabase admin \\"
echo "    --eval 'db.adminCommand({getParameter:1, logComponentVerbosity:1})'"
echo ""
echo "  # Profiling status (expect: was: 0, slowms: 100)"
echo "  kubectl -n ${NAMESPACE} exec mongodb-replica-0 -- \\"
echo "    mongosh --quiet -u ${ADMIN_USER} -p '<password>' \\"
echo "    --authenticationDatabase admin \\"
echo "    --eval 'db.getProfilingStatus()'"
echo ""
echo "  # ConfigMap (expect: journalCompressor: snappy, no operationProfiling/systemLog blocks)"
echo "  kubectl -n ${NAMESPACE} get configmap ${CONFIGMAP} -o yaml"
