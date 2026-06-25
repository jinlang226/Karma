#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongo-rs}"
service="${BENCH_PARAM_SERVICE_NAME:-mongo}"
start="${BENCH_PARAM_START_REPLICAS:-3}"
target=$((start - 1))
removed=$target
primary=""

for ordinal in $(seq 0 $((start - 1))); do
  if kubectl -n "$ns" exec "${cluster}-${ordinal}" -- mongosh --quiet \
    --eval 'db.hello().isWritablePrimary' 2>/dev/null | grep -qx true; then
    primary="${cluster}-${ordinal}"
    break
  fi
done
test -n "$primary"

host="${cluster}-${removed}.${service}.${ns}.svc.cluster.local:27017"
kubectl -n "$ns" exec "$primary" -- mongosh --quiet --eval "rs.remove(\"${host}\")"
kubectl -n "$ns" scale "statefulset/${cluster}" --replicas="$target"
kubectl -n "$ns" rollout status "statefulset/${cluster}" --timeout=600s

printf 'decommissioned one MongoDB member\n' > submit.txt
