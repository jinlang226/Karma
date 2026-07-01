#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/readonly-audit
# Strategy: native_shell
# Notes: Read-only auditor. It captures the live MongoDB topology and
# configuration into ConfigMap/config-audit without mutating the cluster.

static_solver_export_namespace_if_unset "mongodb"

ns="${BENCH_NAMESPACE}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
service="${BENCH_PARAM_HEADLESS_SERVICE_NAME:-${cluster}-svc}"
replica_set="${BENCH_PARAM_REPLICA_SET_NAME:-mongodb-replica}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"

resolve_configmap_name() {
  local candidate=""
  for candidate in \
    "${BENCH_PARAM_MONGOD_CONFIGMAP_NAME:-}" \
    "${BENCH_PARAM_CONFIGMAP_NAME:-}" \
    "${cluster}-mongod-config" \
    "mongod-config"
  do
    [[ -n "${candidate}" ]] || continue
    if kubectl -n "${ns}" get configmap "${candidate}" >/dev/null 2>&1; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  printf '%s\n' "mongod-config"
}

configmap_name="$(resolve_configmap_name)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

admin_password="$(
  kubectl -n "${ns}" get secret "${admin_secret}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
)"
if [[ -z "${admin_password}" ]]; then
  admin_password="${BENCH_PARAM_ADMIN_PASSWORD:-admin123password}"
fi
mongo_uri="mongodb://${admin_user}:${admin_password}@localhost:27017/admin?directConnection=true"

capture_cmd() {
  local name="${1:?output name is required}"
  shift
  "$@" > "${tmp_dir}/${name}" 2>&1 || true
}

capture_mongosh() {
  local name="${1:?output name is required}"
  local eval_str="${2:?mongosh eval string is required}"
  capture_cmd "${name}" \
    kubectl -n "${ns}" exec "${cluster}-0" -- \
    mongosh --quiet "${mongo_uri}" --eval "${eval_str}"
}

capture_cmd pods.txt kubectl -n "${ns}" get pods -o wide
capture_cmd services.txt kubectl -n "${ns}" get svc
capture_cmd statefulset.yaml kubectl -n "${ns}" get statefulset "${cluster}" -o yaml
capture_cmd configmap.yaml kubectl -n "${ns}" get configmap "${configmap_name}" -o yaml
capture_cmd secrets.txt kubectl -n "${ns}" get secret
capture_mongosh rs-status.json 'print(EJSON.stringify(rs.status(), null, 2))'
capture_mongosh rs-conf.json 'print(EJSON.stringify(rs.conf(), null, 2))'
capture_mongosh users.json 'print(EJSON.stringify(db.getSiblingDB("admin").runCommand({usersInfo: 1}), null, 2))'
capture_mongosh roles.json 'print(EJSON.stringify(db.getSiblingDB("admin").runCommand({rolesInfo: 1, showBuiltinRoles: false, showPrivileges: true}), null, 2))'
capture_mongosh log-verbosity.json 'print(EJSON.stringify(db.adminCommand({getParameter: 1, logComponentVerbosity: 1}), null, 2))'
capture_mongosh slow-threshold.json 'print(EJSON.stringify(db.adminCommand({getParameter: 1, slowOpThresholdMs: 1}), null, 2))'
capture_mongosh cmdline-opts.json 'print(EJSON.stringify(db.adminCommand({getCmdLineOpts: 1}), null, 2))'

findings_path="${STATIC_SOLVER_STAGE_DIR}/findings.txt"
{
  printf 'MongoDB Read-Only Audit\n'
  printf 'Namespace: %s\n' "${ns}"
  printf 'Cluster prefix: %s\n' "${cluster}"
  printf 'Headless service: %s\n' "${service}"
  printf 'Replica set name: %s\n' "${replica_set}"
  printf 'ConfigMap snapshot source: %s\n\n' "${configmap_name}"

  printf '=== Pod Overview ===\n%s\n\n' "$(cat "${tmp_dir}/pods.txt")"
  printf '=== Service Overview ===\n%s\n\n' "$(cat "${tmp_dir}/services.txt")"
  printf '=== Secret Inventory ===\n%s\n\n' "$(cat "${tmp_dir}/secrets.txt")"
  printf '=== Replica Set Status ===\n%s\n\n' "$(cat "${tmp_dir}/rs-status.json")"
  printf '=== Replica Set Config ===\n%s\n\n' "$(cat "${tmp_dir}/rs-conf.json")"
  printf '=== Admin Users ===\n%s\n\n' "$(cat "${tmp_dir}/users.json")"
  printf '=== Custom Roles ===\n%s\n\n' "$(cat "${tmp_dir}/roles.json")"
  printf '=== Log Verbosity ===\n%s\n\n' "$(cat "${tmp_dir}/log-verbosity.json")"
  printf '=== Slow Operation Threshold ===\n%s\n\n' "$(cat "${tmp_dir}/slow-threshold.json")"
  printf '=== mongod Command-Line Options ===\n%s\n\n' "$(cat "${tmp_dir}/cmdline-opts.json")"
  printf '=== StatefulSet Snapshot ===\n%s\n\n' "$(cat "${tmp_dir}/statefulset.yaml")"
  printf '=== ConfigMap Snapshot ===\n%s\n\n' "$(cat "${tmp_dir}/configmap.yaml")"
  printf '=== Audit Notes ===\n'
  printf '%s\n' '- This report is read-only. No MongoDB resources, users, or settings were changed.'
  printf '%s\n' '- Compare the captured log verbosity, slowOpThresholdMs, and WiredTiger journal compressor against the expected workflow baseline before approving any follow-up work.'
} > "${findings_path}"

kubectl -n "${ns}" create configmap config-audit \
  --from-file=findings.txt="${findings_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "prepared config-audit ConfigMap"
