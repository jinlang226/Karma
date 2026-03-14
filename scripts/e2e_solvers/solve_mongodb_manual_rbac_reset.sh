#!/usr/bin/env bash
set -euo pipefail

ns="${BENCH_NAMESPACE:-mongodb}"
cm_name="${BENCH_PARAM_RESET_SCRIPT_CONFIGMAP_NAME:-mongodb-rbac-reset-script}"
cm_key="${BENCH_PARAM_RESET_SCRIPT_KEY:-reset_rbac.sh}"

cat >/tmp/reset_rbac.sh <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

mode=""
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) mode="${2:-}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$mode" ] || { echo "missing --mode" >&2; exit 2; }
case "$mode" in all|app|reporting) ;; *) echo "invalid --mode: $mode" >&2; exit 2 ;; esac

ns="${BENCH_NAMESPACE:-mongodb}"
pod="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}-0"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
app_user="${BENCH_PARAM_APP_USERNAME:-app-user}"
reporting_user="${BENCH_PARAM_REPORTING_USERNAME:-reporting-user}"
app_db="${BENCH_PARAM_APP_DATABASE:-appdb}"
reports_coll="${BENCH_PARAM_REPORTS_COLLECTION:-reports}"
reporting_role="${BENCH_PARAM_REPORTING_ROLE_NAME:-reportingRole}"
bad_role="${BENCH_PARAM_BAD_ROLE_NAME:-rawRead}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
app_secret="${BENCH_PARAM_APP_SECRET_NAME:-app-user-password}"
reporting_secret="${BENCH_PARAM_REPORTING_SECRET_NAME:-reporting-user-password}"

admin_pw=$(kubectl -n "$ns" get secret "$admin_secret" -o jsonpath='{.data.password}' | python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.read().strip()).decode())')
app_pw=$(kubectl -n "$ns" get secret "$app_secret" -o jsonpath='{.data.password}' | python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.read().strip()).decode())')
reporting_pw=$(kubectl -n "$ns" get secret "$reporting_secret" -o jsonpath='{.data.password}' | python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.read().strip()).decode())')

ops=()
if [ "$mode" = "all" ] || [ "$mode" = "app" ]; then
  ops+=("try { db.getSiblingDB('admin').createUser({user:'${app_user}',pwd:'${app_pw}',roles:[{role:'readWrite',db:'${app_db}'}]}); } catch (e) { db.getSiblingDB('admin').updateUser('${app_user}', {pwd:'${app_pw}', roles:[{role:'readWrite',db:'${app_db}'}]}); }")
fi
if [ "$mode" = "all" ] || [ "$mode" = "reporting" ]; then
  ops+=("try { db.getSiblingDB('${app_db}').createRole({role:'${reporting_role}',privileges:[{resource:{db:'${app_db}',collection:'${reports_coll}'},actions:['find']}],roles:[]}); } catch (e) { db.getSiblingDB('${app_db}').updateRole('${reporting_role}', {privileges:[{resource:{db:'${app_db}',collection:'${reports_coll}'},actions:['find']}],roles:[]}); }")
  ops+=("try { db.getSiblingDB('admin').createUser({user:'${reporting_user}',pwd:'${reporting_pw}',roles:[{role:'${reporting_role}',db:'${app_db}'}]}); } catch (e) { db.getSiblingDB('admin').updateUser('${reporting_user}', {pwd:'${reporting_pw}', roles:[{role:'${reporting_role}',db:'${app_db}'}]}); }")
  ops+=("try { db.getSiblingDB('admin').revokeRolesFromUser('${reporting_user}', [{role:'${bad_role}',db:'${app_db}'}]); } catch (e) {}")
  ops+=("try { db.getSiblingDB('${app_db}').dropRole('${bad_role}'); } catch (e) {}")
fi
js=$(printf '%s\n' "${ops[@]}")
kubectl -n "$ns" exec "$pod" -- mongosh --quiet "mongodb://${admin_user}:${admin_pw}@localhost:27017/admin" --eval "$js"
SCRIPT

chmod +x /tmp/reset_rbac.sh
kubectl -n "$ns" create configmap "$cm_name" --from-file="$cm_key=/tmp/reset_rbac.sh" --dry-run=client -o yaml | kubectl -n "$ns" apply -f -

touch submit.signal
while [ ! -f submit_result.json ]; do
  sleep 0.2
done
