#!/usr/bin/env bash
#
# rollback.sh — Revert the mongodb replica set to MongoDB defaults.
#
# PURPOSE
#   Rehearsal / review artifact. Reverts the four non-default settings that were
#   applied to the `mongodb` namespace replica set back to MongoDB 6.0 defaults:
#
#     1. systemLog.verbosity        1   -> 0      (default log verbosity)
#     2. operationProfiling         slowOp/200ms  -> off / 100ms (default)
#     3. journalCompressor          zlib -> snappy (default WiredTiger compressor)
#     4. provisioned admin user     admin-user    -> removed (no default user)
#
# SAFETY
#   This script is DESTRUCTIVE to the current configuration. Do NOT run it
#   unless a change window is open and the team has agreed to revert. It is
#   stored in the `rollback-rehearsal` ConfigMap for review only.
#
#   Items 1 & 2 are applied live (no restart). Items 3 & 4 require a config
#   change + rolling restart of the StatefulSet, because journalCompressor is a
#   startup option. Dropping the admin user is performed LAST, because once it
#   is gone you can no longer authenticate while security.authorization is
#   enabled — re-provision a user before doing anything else afterwards.
#
set -euo pipefail

NS="mongodb"
STS="mongodb-replica"
CM="mongod-config"
PODS=(mongodb-replica-0 mongodb-replica-1 mongodb-replica-2)

# Admin credentials are read from the existing secret.
PW="$(kubectl -n "$NS" get secret admin-user-password -o jsonpath='{.data.password}' | base64 -d)"
URI="mongodb://admin-user:${PW}@localhost:27017/admin"

mongo_eval() {
  # Run a mongosh eval against the primary (replica-0 is configured primary).
  kubectl -n "$NS" exec "${PODS[0]}" -- mongosh --quiet "$URI" --eval "$1"
}

echo "==> [1/4] Reset log verbosity to default (0) on every member"
for p in "${PODS[@]}"; do
  kubectl -n "$NS" exec "$p" -- mongosh --quiet "$URI" \
    --eval 'db.adminCommand({setParameter:1, logComponentVerbosity:{verbosity:0}})'
done

echo "==> [2/4] Reset operation profiling to default (off, slowms=100)"
# setProfilingLevel is per-node; apply it on each member.
for p in "${PODS[@]}"; do
  kubectl -n "$NS" exec "$p" -- mongosh --quiet "$URI" \
    --eval 'db.setProfilingLevel(0, {slowms:100})'
done

echo "==> [3/4] Revert mongod.conf to defaults in ConfigMap, then rolling restart"
# Rewrite the ConfigMap with default verbosity, default journal compressor
# (snappy), and operationProfiling removed (default = off / 100ms). Auth,
# keyFile, replication and bind settings are part of the secure baseline and
# are intentionally preserved.
kubectl -n "$NS" create configmap "$CM" --dry-run=client -o yaml \
  --from-literal=mongod.conf='storage:
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
systemLog:
  verbosity: 0
' | kubectl apply -f -

# Roll the StatefulSet so the new config + journalCompressor take effect.
# NOTE: journalCompressor only governs newly written journal files; existing
# files are read with their original compressor, so this is safe online.
kubectl -n "$NS" rollout restart statefulset "$STS"
kubectl -n "$NS" rollout status statefulset "$STS" --timeout=300s

echo "==> [4/4] Remove the provisioned admin user (LAST — locks out auth)"
# Run this only after confirming a replacement access path exists. With
# security.authorization still enabled, removing the sole admin user means no
# further authenticated commands are possible until a new user is created via
# the localhost exception or keyfile/restart procedure.
mongo_eval 'db.getSiblingDB("admin").dropUser("admin-user")'

echo "==> Rollback complete. Replica set reverted to MongoDB defaults."
