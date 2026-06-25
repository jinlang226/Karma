#!/bin/sh
set -eu
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/reset_rbac.sh" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
mode=all
if [[ "${1:-}" == "--mode" ]]; then mode="${2:-}"; fi
case "$mode" in all|app|reporting) ;; *) echo "unsupported mode: $mode" >&2; exit 2 ;; esac

ns="${BENCH_NAMESPACE:-${NAMESPACE:?namespace required}}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
app_secret="${BENCH_PARAM_APP_SECRET_NAME:-app-user-password}"
report_secret="${BENCH_PARAM_REPORTING_SECRET_NAME:-reporting-user-password}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
app_user="${BENCH_PARAM_APP_USERNAME:-app-user}"
report_user="${BENCH_PARAM_REPORTING_USERNAME:-reporting-user}"
app_db="${BENCH_PARAM_APP_DATABASE:-appdb}"
reports="${BENCH_PARAM_REPORTS_COLLECTION:-reports}"
report_role="${BENCH_PARAM_REPORTING_ROLE_NAME:-reportingRole}"

decode_secret() {
  kubectl -n "$ns" get secret "$1" -o jsonpath='{.data.password}' | base64 -d
}
admin_pw=$(decode_secret "$admin_secret")
app_pw=$(decode_secret "$app_secret")
report_pw=$(decode_secret "$report_secret")
uri="mongodb://${admin_user}:${admin_pw}@localhost:27017/admin"

if [[ "$mode" == "all" || "$mode" == "app" ]]; then
  kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet "$uri" --eval "
  const admin=db.getSiblingDB('admin');
  try {
    admin.createUser({user:'${app_user}',pwd:'${app_pw}',roles:[{role:'readWrite',db:'${app_db}'}]});
  } catch (e) {
    admin.updateUser('${app_user}',{pwd:'${app_pw}',roles:[{role:'readWrite',db:'${app_db}'}]});
  }
  "
fi

if [[ "$mode" == "all" || "$mode" == "reporting" ]]; then
  kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet "$uri" --eval "
  const app=db.getSiblingDB('${app_db}');
  const admin=db.getSiblingDB('admin');
  const privileges=[{resource:{db:'${app_db}',collection:'${reports}'},actions:['find']}];
  if (app.getRole('${report_role}')) {
    app.updateRole('${report_role}',{privileges:privileges,roles:[]});
  } else {
    app.createRole({role:'${report_role}',privileges:privileges,roles:[]});
  }
  try {
    admin.createUser({user:'${report_user}',pwd:'${report_pw}',roles:[{role:'${report_role}',db:'${app_db}'}]});
  } catch (e) {
    admin.updateUser('${report_user}',{pwd:'${report_pw}',roles:[{role:'${report_role}',db:'${app_db}'}]});
  }
  "
fi
SCRIPT
chmod +x "$tmp/reset_rbac.sh"
kubectl -n "$BENCH_NAMESPACE" create configmap mongodb-rbac-reset-script \
  --from-file=reset_rbac.sh="$tmp/reset_rbac.sh" \
  --dry-run=client -o yaml | kubectl apply -f -
printf 'installed reusable MongoDB RBAC reset script\n' > submit.txt
