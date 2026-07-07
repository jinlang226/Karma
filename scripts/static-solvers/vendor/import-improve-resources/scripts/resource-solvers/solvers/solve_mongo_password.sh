#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
app_secret="${BENCH_PARAM_APP_SECRET_NAME:-app-user-password}"
next_secret="${BENCH_PARAM_APP_NEXT_SECRET_NAME:-app-user-password-next}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
app_user="${BENCH_PARAM_APP_USERNAME:-app-user}"
app_db="${BENCH_PARAM_APP_DATABASE:-appdb}"
admin_pw=$(kubectl -n "$ns" get secret "$admin_secret" -o jsonpath='{.data.password}' | base64 -d)
next_pw=$(kubectl -n "$ns" get secret "$next_secret" -o jsonpath='{.data.password}' | base64 -d)
kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet \
  "mongodb://${admin_user}:${admin_pw}@localhost:27017/admin" --eval \
  "db.getSiblingDB('admin').updateUser('${app_user}',{pwd:'${next_pw}',roles:[{role:'readWrite',db:'${app_db}'}]})"
kubectl -n "$ns" create secret generic "$app_secret" \
  --from-literal=password="$next_pw" --dry-run=client -o yaml |
  kubectl -n "$ns" apply -f -
printf 'rotated MongoDB application password\n' > submit.txt
