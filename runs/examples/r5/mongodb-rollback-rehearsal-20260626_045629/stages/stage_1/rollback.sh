#!/usr/bin/env bash
# rollback.sh — Revert MongoDB replica set non-default configuration to MongoDB defaults
#
# Reverts the following non-default settings:
#   1. systemLog.verbosity 1        → 0           (MongoDB default)
#   2. operationProfiling mode      → off          (MongoDB default)
#      slowOpThresholdMs            → 100 ms       (MongoDB default)
#   3. wiredTiger journalCompressor → snappy       (MongoDB default, was: zlib)
#   4. Drops the provisioned admin-user account
#
# Steps 1–2 are applied at runtime (no restart needed).
# Step 3 patches the mongod-config ConfigMap, then performs a rolling restart.
# Step 4 drops the admin-user; see the WARNING below before running.
#
# Requires: kubectl configured against the target cluster
# DO NOT EXECUTE until the change window is approved and reviewed.
#
# WARNING (Step 4): Dropping admin-user will break the pod liveness/readiness probes
# (they connect as admin-user) and leave the replica set with no admin account.
# Only run Step 4 after provisioning a replacement admin account or disabling auth.

set -euo pipefail

NS="mongodb"
STS="mongodb-replica"
PRIMARY="mongodb-replica-0"
MEMBERS=(mongodb-replica-0 mongodb-replica-1 mongodb-replica-2)

# ── Step 0: Verify connectivity ────────────────────────────────────────────────
echo "[rollback] Verifying kubectl access to namespace ${NS}..."
kubectl -n "${NS}" get pods --no-headers -o name

# ── Step 1: Reset runtime settings on every replica member ────────────────────
# logComponentVerbosity and slowOpThresholdMs can be changed without restart.
# db.setProfilingLevel(0) disables the per-database profiler immediately.
echo ""
echo "[rollback] Step 1: Resetting runtime settings on each pod (no restart required)..."
for POD in "${MEMBERS[@]}"; do
  echo "  → ${POD}"
  kubectl -n "${NS}" exec "${POD}" -- bash -c '
    mongosh --quiet \
      -u admin-user -p "${ADMIN_PASSWORD}" \
      --authenticationDatabase admin \
      --eval "
        // 1a. Reset global log verbosity to 0 (default)
        db.adminCommand({ setParameter: 1,
          logComponentVerbosity: { verbosity: 0 } });

        // 1b. Reset global slow-operation threshold to 100 ms (default)
        db.adminCommand({ setParameter: 1, slowOpThresholdMs: 100 });

        // 1c. Disable the profiler on the admin database (level 0 = off, default)
        db.setProfilingLevel(0);

        print(\"[" + db.getMongo().host + "] runtime settings reset.\");
      "
  '
done

# ── Step 2: Restore the mongod-config ConfigMap to default values ──────────────
# Removes: journalCompressor override, systemLog.verbosity, operationProfiling.
# WiredTiger will use snappy (the built-in default) for new journal writes after
# the restart; existing journal segments are managed transparently by WiredTiger.
echo ""
echo "[rollback] Step 2: Patching mongod-config ConfigMap to restore defaults..."
kubectl -n "${NS}" apply -f - <<'CONFIGMAP'
apiVersion: v1
kind: ConfigMap
metadata:
  name: mongod-config
  namespace: mongodb
data:
  mongod.conf: |
    storage:
      dbPath: /data/db
    net:
      bindIpAll: true
    replication:
      replSetName: mongodb-replica
    security:
      authorization: enabled
      keyFile: /etc/mongo-keyfile/keyfile
CONFIGMAP

# ── Step 3: Rolling restart to load the updated mongod.conf ───────────────────
echo ""
echo "[rollback] Step 3: Rolling restart of StatefulSet ${STS}..."
kubectl -n "${NS}" rollout restart statefulset/"${STS}"
kubectl -n "${NS}" rollout status statefulset/"${STS}" --timeout=300s
echo "  Rolling restart complete."

# ── Step 4: Drop the provisioned admin-user account ───────────────────────────
# !! READ BEFORE UNCOMMENTING !!
# Removing admin-user will immediately fail the pod liveness and readiness probes,
# and will leave the replica set with no administrative account while auth is
# enabled. Uncomment only after:
#   a) A replacement admin account has been created, AND
#   b) The StatefulSet probe commands have been updated to use the new account.
echo ""
echo "[rollback] Step 4: admin-user removal is GATED — uncomment the block below after"
echo "           satisfying the prerequisites described in the script header."
#
# kubectl -n "${NS}" exec "${PRIMARY}" -- bash -c '
#   mongosh --quiet \
#     -u admin-user -p "${ADMIN_PASSWORD}" \
#     --authenticationDatabase admin \
#     --eval "
#       db.dropUser(\"admin-user\");
#       print(\"admin-user account removed.\");
#     "
# '
#
# After dropping admin-user, also remove (or rotate) the admin-user-password Secret:
#   kubectl -n mongodb delete secret admin-user-password

echo ""
echo "[rollback] Rollback complete."
echo "           Verbosity → 0, profiling → off, slowOpThresholdMs → 100 ms,"
echo "           journalCompressor → snappy (default)."
echo "           admin-user removal: pending manual gate (see Step 4)."
