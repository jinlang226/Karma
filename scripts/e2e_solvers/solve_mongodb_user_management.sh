#!/usr/bin/env bash
set -euo pipefail

ns="${BENCH_NAMESPACE:-mongodb}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
app_secret="${BENCH_PARAM_APP_SECRET_NAME:-app-user-password}"
readonly_secret="${BENCH_PARAM_READONLY_SECRET_NAME:-readonly-user-password}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
app_user="${BENCH_PARAM_APP_USERNAME:-app-user}"
readonly_user="${BENCH_PARAM_READONLY_USERNAME:-readonly-user}"
app_db="${BENCH_PARAM_APP_DATABASE:-appdb}"
reports_collection="${BENCH_PARAM_REPORTS_COLLECTION:-reports}"
reporting_role="${BENCH_PARAM_REPORTING_ROLE_NAME:-reportingRole}"

admin_pw_b64="$(kubectl -n "$ns" get secret "$admin_secret" -o jsonpath='{.data.password}')"
app_pw_b64="$(kubectl -n "$ns" get secret "$app_secret" -o jsonpath='{.data.password}')"
readonly_pw_b64="$(kubectl -n "$ns" get secret "$readonly_secret" -o jsonpath='{.data.password}')"
admin_pw="$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode())' "$admin_pw_b64")"
app_pw="$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode())' "$app_pw_b64")"
readonly_pw="$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode())' "$readonly_pw_b64")"

kubectl -n "$ns" exec "$cluster-0" -- mongosh --quiet "mongodb://${admin_user}:${admin_pw}@localhost:27017/admin" --eval "
try { db.getSiblingDB('${app_db}').createRole({role:'${reporting_role}', privileges:[{resource:{db:'${app_db}', collection:'${reports_collection}'}, actions:['find']}], roles:[]}); }
catch (e) { db.getSiblingDB('${app_db}').updateRole('${reporting_role}', {privileges:[{resource:{db:'${app_db}', collection:'${reports_collection}'}, actions:['find']}], roles:[]}); }
try { db.getSiblingDB('admin').createUser({user:'${app_user}', pwd:'${app_pw}', roles:[{role:'readWrite', db:'${app_db}'}]}); }
catch (e) { db.getSiblingDB('admin').updateUser('${app_user}', {pwd:'${app_pw}', roles:[{role:'readWrite', db:'${app_db}'}]}); }
try { db.getSiblingDB('admin').createUser({user:'${readonly_user}', pwd:'${readonly_pw}', roles:[{role:'read', db:'${app_db}'},{role:'${reporting_role}', db:'${app_db}'}]}); }
catch (e) { db.getSiblingDB('admin').updateUser('${readonly_user}', {pwd:'${readonly_pw}', roles:[{role:'read', db:'${app_db}'},{role:'${reporting_role}', db:'${app_db}'}]}); }
"

touch submit.signal
while [ ! -f submit_result.json ]; do
  sleep 0.2
done
