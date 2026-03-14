#!/usr/bin/env bash
set -euo pipefail

ns="${BENCH_NAMESPACE:-mongodb}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
reporting_secret="${BENCH_PARAM_REPORTING_SECRET_NAME:-reporting-user-password}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
reporting_user="${BENCH_PARAM_REPORTING_USERNAME:-reporting-user}"
app_db="${BENCH_PARAM_APP_DATABASE:-appdb}"
reports_collection="${BENCH_PARAM_REPORTS_COLLECTION:-reports}"
raw_collection="${BENCH_PARAM_RAW_COLLECTION:-raw}"
bad_role="${BENCH_PARAM_BAD_ROLE_NAME:-rawRead}"
reporting_role="${BENCH_PARAM_REPORTING_ROLE_NAME:-reportingRole}"

admin_pw_b64="$(kubectl -n "$ns" get secret "$admin_secret" -o jsonpath='{.data.password}')"
reporting_pw_b64="$(kubectl -n "$ns" get secret "$reporting_secret" -o jsonpath='{.data.password}')"
admin_pw="$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode())' "$admin_pw_b64")"
reporting_pw="$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode())' "$reporting_pw_b64")"

kubectl -n "$ns" exec "$cluster-0" -- mongosh --quiet "mongodb://${admin_user}:${admin_pw}@localhost:27017/admin" --eval "
try { db.getSiblingDB('${app_db}').createRole({role:'${reporting_role}', privileges:[{resource:{db:'${app_db}', collection:'${reports_collection}'}, actions:['find']}], roles:[]}); }
catch (e) { db.getSiblingDB('${app_db}').updateRole('${reporting_role}', {privileges:[{resource:{db:'${app_db}', collection:'${reports_collection}'}, actions:['find']}], roles:[]}); }
try { db.getSiblingDB('admin').createUser({user:'${reporting_user}', pwd:'${reporting_pw}', roles:[{role:'${reporting_role}', db:'${app_db}'}]}); }
catch (e) { db.getSiblingDB('admin').updateUser('${reporting_user}', {pwd:'${reporting_pw}', roles:[{role:'${reporting_role}', db:'${app_db}'}]}); }
try { db.getSiblingDB('admin').revokeRolesFromUser('${reporting_user}', [{role:'${bad_role}', db:'${app_db}'}]); } catch (e) {}
try { db.getSiblingDB('${app_db}').dropRole('${bad_role}'); } catch (e) {}
"

touch submit.signal
while [ ! -f submit_result.json ]; do
  sleep 0.2
done
