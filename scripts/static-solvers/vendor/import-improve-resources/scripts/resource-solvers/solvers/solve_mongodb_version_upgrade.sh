#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
to_image="${BENCH_PARAM_TO_IMAGE:-mongo:6.0.5}"
to_fcv="${BENCH_PARAM_TO_FCV:-6.0}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
admin_pw=$(kubectl -n "$ns" get secret "$admin_secret" -o jsonpath='{.data.password}' | base64 --decode)
admin_uri="mongodb://${admin_user}:${admin_pw}@localhost:27017/admin"

kubectl -n "$ns" set image "statefulset/${cluster}" "mongod=${to_image}"
kubectl -n "$ns" rollout status "statefulset/${cluster}" --timeout=600s

primary=""
for _ in $(seq 1 60); do
  for index in 0 1 2; do
    pod="${cluster}-${index}"
    if kubectl -n "$ns" exec "$pod" -- mongosh --quiet "$admin_uri" \
      --eval 'db.hello().isWritablePrimary' 2>/dev/null | grep -qx true; then
      primary="$pod"
      break
    fi
  done
  [ -n "$primary" ] && break
  sleep 3
done
[ -n "$primary" ]

kubectl -n "$ns" exec "$primary" -- mongosh --quiet "$admin_uri" \
  --eval "db.adminCommand({setFeatureCompatibilityVersion: \"${to_fcv}\"})" >/dev/null

for _ in $(seq 1 60); do
  if kubectl -n "$ns" exec "$primary" -- mongosh --quiet "$admin_uri" --eval '
    (() => {
      const status = rs.status();
      return status.members.filter(member => member.stateStr === "PRIMARY").length === 1 &&
        status.members.filter(member => member.stateStr === "SECONDARY").length === 2;
    })()' | grep -qx true; then
    printf 'upgraded MongoDB and finalized FCV\n' > submit.txt
    exit 0
  fi
  sleep 3
done

exit 1
