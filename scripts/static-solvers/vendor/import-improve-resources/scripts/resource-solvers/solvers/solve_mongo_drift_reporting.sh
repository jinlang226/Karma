#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
report_user="${BENCH_PARAM_REPORTING_USERNAME:-reporting-user}"
app_db="${BENCH_PARAM_APP_DATABASE:-appdb}"
raw="${BENCH_PARAM_RAW_COLLECTION:-raw}"
bad_role="${BENCH_PARAM_BAD_ROLE_NAME:-rawRead}"
report_role="${BENCH_PARAM_REPORTING_ROLE_NAME:-reportingRole}"
admin_pw=$(kubectl -n "$ns" get secret "$admin_secret" -o jsonpath='{.data.password}' | base64 -d)
kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet \
  "mongodb://${admin_user}:${admin_pw}@localhost:27017/admin" --eval "
const app=db.getSiblingDB('${app_db}');
const admin=db.getSiblingDB('admin');
const privileges=[{resource:{db:'${app_db}',collection:'${raw}'},actions:['find']}];
if (app.getRole('${bad_role}')) {
  app.updateRole('${bad_role}',{privileges:privileges,roles:[]});
} else {
  app.createRole({role:'${bad_role}',privileges:privileges,roles:[]});
}
try { app.dropRole('${report_role}'); } catch (e) {}
try { admin.dropRole('${report_role}'); } catch (e) {}
admin.updateUser('${report_user}',{roles:[{role:'${bad_role}',db:'${app_db}'}]});
"
printf 'planted MongoDB reporting RBAC drift\n' > submit.txt
