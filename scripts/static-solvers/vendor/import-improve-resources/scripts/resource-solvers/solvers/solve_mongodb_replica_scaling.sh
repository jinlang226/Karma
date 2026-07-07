#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
service="${BENCH_PARAM_HEADLESS_SERVICE_NAME:-mongodb-replica-svc}"
target="${BENCH_PARAM_TARGET_REPLICAS:-5}"
password=$(kubectl -n "$ns" get secret admin-user-password -o jsonpath='{.data.password}' | base64 -d)
kubectl -n "$ns" scale "statefulset/${cluster}" --replicas="$target"
for ordinal in $(seq 0 $((target - 1))); do
  for _ in $(seq 1 120); do
    phase=$(kubectl -n "$ns" get pod "${cluster}-${ordinal}" -o jsonpath='{.status.phase}' 2>/dev/null || true)
    [ "$phase" = Running ] && break
    sleep 3
  done
done
for ordinal in $(seq 0 $((target - 1))); do
  primary_host=$(kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet \
    "mongodb://admin-user:${password}@localhost:27017/admin?directConnection=true" \
    --eval 'rs.hello().primary')
  primary_pod=${primary_host%%.*}
  host="${cluster}-${ordinal}.${service}.${ns}.svc.cluster.local:27017"
  exists=$(kubectl -n "$ns" exec "$primary_pod" -- mongosh --quiet \
    "mongodb://admin-user:${password}@localhost:27017/admin?directConnection=true" \
    --eval "rs.conf().members.some(m=>m._id===${ordinal})")
  if [ "$exists" != true ]; then
    kubectl -n "$ns" exec "$primary_pod" -- mongosh --quiet \
      "mongodb://admin-user:${password}@localhost:27017/admin?directConnection=true" \
      --eval "rs.add({_id:${ordinal},host:\"${host}\"})"
    for _ in $(seq 1 120); do
      state=$(kubectl -n "$ns" exec "$primary_pod" -- mongosh --quiet \
        "mongodb://admin-user:${password}@localhost:27017/admin?directConnection=true" \
        --eval "rs.status().members.find(m=>m._id===${ordinal})?.stateStr" 2>/dev/null || true)
      if [ "$state" = SECONDARY ] || [ "$state" = PRIMARY ]; then
        break
      fi
      sleep 3
    done
  fi
done
primary_host=$(kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet \
  "mongodb://admin-user:${password}@localhost:27017/admin?directConnection=true" \
  --eval 'rs.hello().primary')
primary_pod=${primary_host%%.*}
for _ in $(seq 1 120); do
  count=$(kubectl -n "$ns" exec "$primary_pod" -- mongosh --quiet \
    "mongodb://admin-user:${password}@localhost:27017/admin?directConnection=true" \
    --eval 'rs.status().members.filter(m=>m.stateStr==="PRIMARY"||m.stateStr==="SECONDARY").length' 2>/dev/null || true)
  [ "$count" = "$target" ] && break
  sleep 3
done
printf 'scaled MongoDB replica set to %s members\n' "$target" > submit.txt
