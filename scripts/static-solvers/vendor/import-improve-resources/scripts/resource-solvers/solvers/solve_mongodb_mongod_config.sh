#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
rs="${BENCH_PARAM_REPLICA_SET_NAME:-mongodb-replica}"
log_level="${BENCH_PARAM_TARGET_LOG_LEVEL:-1}"
slow_ms="${BENCH_PARAM_TARGET_SLOW_MS:-200}"
compressor="${BENCH_PARAM_TARGET_JOURNAL_COMPRESSOR:-zlib}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/mongod.conf" <<EOF
storage:
  dbPath: /data/db
  wiredTiger:
    engineConfig:
      journalCompressor: ${compressor}
net:
  bindIpAll: true
replication:
  replSetName: ${rs}
security:
  authorization: enabled
  keyFile: /etc/mongo-keyfile/keyfile
systemLog:
  verbosity: ${log_level}
operationProfiling:
  mode: slowOp
  slowOpThresholdMs: ${slow_ms}
EOF
kubectl -n "$ns" create configmap "${cluster}-mongod-config" --from-file="$tmp/mongod.conf" \
  --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
kubectl -n "$ns" rollout restart "statefulset/${cluster}"
kubectl -n "$ns" rollout status "statefulset/${cluster}" --timeout=600s
printf 'updated mongod configuration\n' > submit.txt
