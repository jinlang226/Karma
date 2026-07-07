#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/user-management
# Strategy: native_shell
# Notes: Reconcile the reporting role plus app/readonly users against the live
# primary so workflow-inherited clusters that skipped the standalone seed path
# still get the required users, secrets, and role bindings.

static_solver_export_namespace_if_unset "mongodb"

ns="${BENCH_NAMESPACE}"
configured_cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
app_secret="${BENCH_PARAM_APP_SECRET_NAME:-app-user-password}"
readonly_secret="${BENCH_PARAM_READONLY_SECRET_NAME:-readonly-user-password}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
app_user="${BENCH_PARAM_APP_USERNAME:-app-user}"
readonly_user="${BENCH_PARAM_READONLY_USERNAME:-readonly-user}"
app_db="${BENCH_PARAM_APP_DATABASE:-appdb}"
reports_collection="${BENCH_PARAM_REPORTS_COLLECTION:-reports}"
reporting_role="${BENCH_PARAM_REPORTING_ROLE_NAME:-reportingRole}"
default_admin_password="${BENCH_PARAM_ADMIN_PASSWORD:-admin123password}"
default_app_password="${BENCH_PARAM_APP_PASSWORD:-app123password}"
default_readonly_password="${BENCH_PARAM_READONLY_PASSWORD:-readonly123password}"

cluster="${configured_cluster}"
if ! kubectl -n "${ns}" get statefulset "${cluster}" >/dev/null 2>&1; then
  cluster="$(kubectl -n "${ns}" get statefulset -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
fi
[[ -n "${cluster}" ]] || static_solver_fail "unable to locate a MongoDB statefulset in namespace ${ns}"

service_name="$(
  kubectl -n "${ns}" get statefulset "${cluster}" -o jsonpath='{.spec.serviceName}' 2>/dev/null || true
)"
service_name="${service_name:-${BENCH_PARAM_HEADLESS_SERVICE_NAME:-${cluster}-svc}}"
expected_replicas="$(
  kubectl -n "${ns}" get statefulset "${cluster}" -o jsonpath='{.spec.replicas}' 2>/dev/null || printf '3'
)"
[[ "${expected_replicas}" =~ ^[0-9]+$ ]] || expected_replicas="3"

ensure_secret_password() {
  local secret_name="${1:?secret name is required}"
  local default_password="${2:?default password is required}"
  local current_password=""

  current_password="$(
    kubectl -n "${ns}" get secret "${secret_name}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
  )"
  if [[ -n "${current_password}" ]]; then
    printf '%s\n' "${current_password}"
    return 0
  fi

  kubectl -n "${ns}" create secret generic "${secret_name}" \
    --from-literal=password="${default_password}" \
    --dry-run=client -o yaml | kubectl -n "${ns}" apply -f - >/dev/null
  printf '%s\n' "${default_password}"
}

admin_password="$(ensure_secret_password "${admin_secret}" "${default_admin_password}")"
app_password="$(ensure_secret_password "${app_secret}" "${default_app_password}")"
readonly_password="$(ensure_secret_password "${readonly_secret}" "${default_readonly_password}")"

mongo_tls_flags=()
for ca_path in /etc/tls/ca.crt /etc/mongo-ca/ca.crt /etc/mongodb/tls/ca.crt /etc/ssl/mongodb/ca.crt; do
  if kubectl -n "${ns}" exec "${cluster}-0" -- /bin/sh -c "test -f ${ca_path}" >/dev/null 2>&1; then
    mongo_tls_flags=(--tls --tlsAllowInvalidHostnames --tlsAllowInvalidCertificates --tlsCAFile "${ca_path}")
    for client_pem in /etc/tls/client.pem /etc/mongo-ca/client.pem; do
      if kubectl -n "${ns}" exec "${cluster}-0" -- /bin/sh -c "test -f ${client_pem}" >/dev/null 2>&1; then
        mongo_tls_flags+=(--tlsCertificateKeyFile "${client_pem}")
        break
      fi
    done
    break
  fi
done

mongosh_base=(mongosh --quiet)
if [[ ${#mongo_tls_flags[@]} -gt 0 ]]; then
  mongosh_base+=("${mongo_tls_flags[@]}")
fi
mongosh_base+=("mongodb://${admin_user}:${admin_password}@localhost:27017/admin?directConnection=true")

mongo_eval() {
  local pod="${1:?pod is required}"
  local eval_str="${2:?eval string is required}"
  kubectl -n "${ns}" exec "${pod}" -- "${mongosh_base[@]}" --eval "${eval_str}"
}

find_primary() {
  local ordinal pod
  for ((ordinal = 0; ordinal < expected_replicas; ordinal++)); do
    pod="${cluster}-${ordinal}"
    if mongo_eval "${pod}" 'db.hello().isWritablePrimary' 2>/dev/null | grep -q true; then
      printf '%s\n' "${pod}"
      return 0
    fi
  done
  return 1
}

primary_pod=""
for _ in $(seq 1 60); do
  primary_pod="$(find_primary || true)"
  if [[ -n "${primary_pod}" ]]; then
    break
  fi
  sleep 3
done
[[ -n "${primary_pod}" ]] || static_solver_fail "unable to locate a writable primary before reconciling MongoDB users"

mongo_eval "${primary_pod}" "
  const admin = db.getSiblingDB('admin');
  const app = db.getSiblingDB('${app_db}');
  const privileges = [
    {
      resource: {db: '${app_db}', collection: '${reports_collection}'},
      actions: ['find']
    }
  ];

  if (admin.getRole('${reporting_role}', {showPrivileges: true})) {
    admin.updateRole('${reporting_role}', {privileges, roles: []});
  }
  if (app.getRole('${reporting_role}', {showPrivileges: true})) {
    app.updateRole('${reporting_role}', {privileges, roles: []});
  } else {
    app.createRole({role: '${reporting_role}', privileges, roles: []});
  }

  function chooseUserDb(username) {
    if (admin.getUser(username)) {
      return admin;
    }
    if (app.getUser(username)) {
      return app;
    }
    return admin;
  }

  function upsertUser(userDb, username, password, roles) {
    if (userDb.getUser(username)) {
      userDb.updateUser(username, {pwd: password, roles});
    } else {
      userDb.createUser({user: username, pwd: password, roles});
    }
  }

  upsertUser(
    chooseUserDb('${app_user}'),
    '${app_user}',
    '${app_password}',
    [{role: 'readWrite', db: '${app_db}'}]
  );

  upsertUser(
    chooseUserDb('${readonly_user}'),
    '${readonly_user}',
    '${readonly_password}',
    [
      {role: 'read', db: '${app_db}'},
      {role: '${reporting_role}', db: '${app_db}'}
    ]
  );
" >/dev/null

static_solver_write_submit "reconciled MongoDB users and reporting role"
