#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
service="${BENCH_PARAM_HEADLESS_SERVICE_NAME:-mongodb-replica-svc}"
rs="${BENCH_PARAM_REPLICA_SET_NAME:-mongodb-replica}"
user="${BENCH_PARAM_REPORTING_USERNAME:-reporting-user}"
db="${BENCH_PARAM_APP_DATABASE:-appdb}"
reports="${BENCH_PARAM_REPORTS_COLLECTION:-reports}"
role="${BENCH_PARAM_REPORTING_ROLE_NAME:-reportingRole}"
admin_password=$(kubectl -n "$ns" get secret admin-user-password -o jsonpath='{.data.password}' | base64 -d)
uri="mongodb://admin-user:${admin_password}@${cluster}-0.${service}:27017,${cluster}-1.${service}:27017,${cluster}-2.${service}:27017/admin?replicaSet=${rs}"
kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet "$uri" --eval "
  const app=db.getSiblingDB(\"${db}\");
  const privileges=[{resource:{db:\"${db}\",collection:\"${reports}\"},actions:[\"find\"]}];
  if (app.getRole(\"${role}\")) {
    app.updateRole(\"${role}\",{privileges,roles:[]})
  } else {
    app.createRole({role:\"${role}\",privileges,roles:[]})
  }
  db.getSiblingDB(\"admin\").updateUser(\"${user}\",{roles:[{role:\"${role}\",db:\"${db}\"}]});
"
printf 'reconciled custom reporting role\n' > submit.txt
