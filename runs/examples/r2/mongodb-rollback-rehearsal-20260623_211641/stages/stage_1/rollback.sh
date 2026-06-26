#!/usr/bin/env bash
# rollback.sh — Revert non-default MongoDB config to MongoDB 6.0 defaults.
#
# Non-default settings in effect (sourced from mongod-config ConfigMap and
# getParameter / getCmdLineOpts inspection on 2026-06-23):
#
#   systemLog.verbosity                               : 1      -> 0       (default)
#   operationProfiling.mode                           : slowOp -> off     (default)
#   operationProfiling.slowOpThresholdMs              : 200 ms -> 100 ms  (default)
#   storage.wiredTiger.engineConfig.journalCompressor : zlib   -> snappy  (default)
#
# NOT reverted:
#   security.authorization / keyFile — required for cluster auth; removing
#     these would leave the replica set inaccessible.
#   admin-user account — cannot be safely dropped while authorization is
#     enabled.  Removing it would lock all clients out immediately.
#   Replica-set member topology (rs.conf) — all members already carry the
#     MongoDB defaults (priority 1, hidden false, secondaryDelaySecs 0,
#     buildIndexes true); nothing to revert there.
#
# Execution order:
#   1. Apply live setParameter changes (verbosity, profiling) on every member
#      — takes effect instantly, no restart needed.
#   2. Patch the mongod-config ConfigMap back to the canonical defaults.
#   3. Rolling restart (secondaries first, then primary) so every pod reloads
#      the config file — required for the journalCompressor switch zlib->snappy.
#
# Prerequisites:
#   - kubectl on PATH, current context pointed at the target cluster.
#   - The admin-user-password Secret exists in the mongodb namespace (it does;
#     this script reads it automatically).
#   - Run from a host that can reach the cluster's API server.

set -euo pipefail

NAMESPACE="mongodb"
ADMIN_USER="admin-user"

# Read the admin password from the Secret so the script stays credential-free.
ADMIN_PASSWORD="$(
  kubectl -n "${NAMESPACE}" get secret admin-user-password \
    -o jsonpath='{.data.password}' | base64 -d
)"

MONGOSH_AUTH="-u ${ADMIN_USER} -p ${ADMIN_PASSWORD} --authenticationDatabase admin"

# ── 1. Live parameter resets (no restart required) ───────────────────────────
# logComponentVerbosity is a global setParameter, effective immediately.
# The profile command is per-database; we reset it on the admin db here for
# instant effect — the ConfigMap patch + rolling restart in steps 2–3 will
# enforce the defaults on all databases at next startup.

echo "==> [1/3] Resetting live parameters on each replica member..."

for pod in mongodb-replica-0 mongodb-replica-1 mongodb-replica-2; do
  echo "    ${pod}: applying setParameter..."
  kubectl -n "${NAMESPACE}" exec "${pod}" -- mongosh --quiet \
    ${MONGOSH_AUTH} --eval '
      // Verbosity 0 is the MongoDB default (was 1).
      db.adminCommand({ setParameter: 1,
        logComponentVerbosity: { verbosity: 0 } });

      // Profiling off, slowOpThresholdMs 100 ms are the MongoDB defaults
      // (were mode:slowOp, 200 ms).  "profile: 0" disables collection.
      db.adminCommand({ profile: 0, slowms: 100 });

      print("  verbosity and profiling reset on " + db.getMongo().host);
    '
done

# ── 2. Patch mongod-config ConfigMap to defaults ─────────────────────────────
# Remove systemLog.verbosity and operationProfiling sections entirely so
# MongoDB uses its compiled-in defaults (verbosity 0, profiling off / 100 ms).
# Switch journalCompressor from zlib back to snappy (MongoDB default).

echo "==> [2/3] Patching mongod-config ConfigMap to default values..."

kubectl apply -f - <<'YAML'
apiVersion: v1
kind: ConfigMap
metadata:
  name: mongod-config
  namespace: mongodb
data:
  mongod.conf: |
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
YAML

echo "    ConfigMap patched."

# ── 3. Rolling restart ────────────────────────────────────────────────────────
# Restart secondaries before the primary so the replica set always has a
# reachable primary during the rollout.  Each pod is deleted and recreated by
# the StatefulSet controller; we wait for Ready before moving to the next.

echo "==> [3/3] Rolling restart — secondaries first, then primary..."

for pod in mongodb-replica-2 mongodb-replica-1 mongodb-replica-0; do
  echo "    Deleting ${pod} (StatefulSet will recreate it)..."
  kubectl -n "${NAMESPACE}" delete pod "${pod}"

  echo "    Waiting for ${pod} to become Ready (timeout 180 s)..."
  kubectl -n "${NAMESPACE}" wait pod "${pod}" \
    --for=condition=Ready --timeout=180s

  echo "    ${pod} is Ready."
  # Allow the rejoined node a moment to catch up before we restart the next.
  sleep 5
done

# ── Verify ────────────────────────────────────────────────────────────────────

echo "==> Verifying final configuration via mongodb-replica-0..."

kubectl -n "${NAMESPACE}" exec mongodb-replica-0 -- mongosh --quiet \
  ${MONGOSH_AUTH} --eval '
    const opts  = db.adminCommand({ getCmdLineOpts: 1 }).parsed;
    const verbR = db.adminCommand({ getParameter: 1, logComponentVerbosity: 1 });

    const compressor = opts.storage.wiredTiger.engineConfig.journalCompressor;
    const verbosity  = verbR.logComponentVerbosity.verbosity;
    const profiling  = opts.operationProfiling
                       ? JSON.stringify(opts.operationProfiling)
                       : "not set (defaults: mode=off, slowOpThresholdMs=100)";

    print("journalCompressor  :", compressor);
    print("systemLog.verbosity:", verbosity);
    print("operationProfiling :", profiling);

    const ok =
      compressor === "snappy" &&
      verbosity  === 0        &&
      !opts.operationProfiling;

    print(ok ? "\nAll settings match MongoDB defaults." : "\nWARNING: one or more settings did not revert as expected.");
  '

echo "==> Rollback complete."
