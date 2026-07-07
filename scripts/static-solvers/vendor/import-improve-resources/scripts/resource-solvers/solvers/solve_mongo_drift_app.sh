#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
app_user="${BENCH_PARAM_APP_USERNAME:-app-user}"
readonly_user="${BENCH_PARAM_READONLY_USERNAME:-readonly-user}"
app_db="${BENCH_PARAM_APP_DATABASE:-appdb}"
admin_pw=$(kubectl -n "$ns" get secret "$admin_secret" -o jsonpath='{.data.password}' | base64 -d)
kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet \
  "mongodb://${admin_user}:${admin_pw}@localhost:27017/admin" --eval "
const admin=db.getSiblingDB('admin');
admin.updateUser('${app_user}',{roles:[{role:'read',db:'${app_db}'}]});
admin.updateUser('${readonly_user}',{roles:[{role:'read',db:'${app_db}'}]});
"
printf 'planted MongoDB application RBAC drift\n' > submit.txt
