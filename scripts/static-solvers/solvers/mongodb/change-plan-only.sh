#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/change-plan-only
# Strategy: native_shell
# Notes: Review-only planner. It captures the live MongoDB state and writes a
# migration plan to ConfigMap/change-plan without mutating the cluster.

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
capture_mongosh rs-status.json 'print(EJSON.stringify(rs.status(), null, 2))'
capture_mongosh rs-conf.json 'print(EJSON.stringify(rs.conf(), null, 2))'
capture_mongosh users.json 'print(EJSON.stringify(db.getSiblingDB("admin").runCommand({usersInfo: 1}), null, 2))'
capture_mongosh roles.json 'print(EJSON.stringify(db.getSiblingDB("admin").runCommand({rolesInfo: 1, showBuiltinRoles: false, showPrivileges: true}), null, 2))'
capture_mongosh log-verbosity.json 'print(EJSON.stringify(db.adminCommand({getParameter: 1, logComponentVerbosity: 1}), null, 2))'
capture_mongosh slow-threshold.json 'print(EJSON.stringify(db.adminCommand({getParameter: 1, slowOpThresholdMs: 1}), null, 2))'
capture_mongosh cmdline-opts.json 'print(EJSON.stringify(db.adminCommand({getCmdLineOpts: 1}), null, 2))'

plan_path="${STATIC_SOLVER_STAGE_DIR}/plan.md"
{
  printf '# MongoDB Change / Migration Plan\n\n'
  printf '## Scope\n'
  printf '%s\n' "- Namespace: \`${ns}\`"
  printf '%s\n' "- Cluster prefix: \`${cluster}\`"
  printf '%s\n' "- Headless service: \`${service}\`"
  printf '%s\n' "- Replica set name: \`${replica_set}\`"
  printf '%s\n\n' "- ConfigMap snapshot source: \`${configmap_name}\`"

  printf '## Current State Summary\n'
  printf '### Pod Overview\n```text\n%s\n```\n\n' "$(cat "${tmp_dir}/pods.txt")"
  printf '### Replica Set Status\n```json\n%s\n```\n\n' "$(cat "${tmp_dir}/rs-status.json")"
  printf '### Current Users\n```json\n%s\n```\n\n' "$(cat "${tmp_dir}/users.json")"
  printf '### Current Custom Roles\n```json\n%s\n```\n\n' "$(cat "${tmp_dir}/roles.json")"
  printf '### Current Log Verbosity\n```json\n%s\n```\n\n' "$(cat "${tmp_dir}/log-verbosity.json")"
  printf '### Current slowOpThresholdMs\n```json\n%s\n```\n\n' "$(cat "${tmp_dir}/slow-threshold.json")"
  printf '### Current mongod Command-Line Options\n```json\n%s\n```\n\n' "$(cat "${tmp_dir}/cmdline-opts.json")"
  printf '### Current ConfigMap Snapshot\n```yaml\n%s\n```\n\n' "$(cat "${tmp_dir}/configmap.yaml")"

  printf '## Proposed Review-Only Change Steps\n'
  printf '1. Reconfirm replica-set health, pod readiness, and admin connectivity immediately before the maintenance window.\n'
  printf '2. Compare the current journal compressor, log verbosity, and slowOpThresholdMs captured above against the hardened target baseline and decide which values must change.\n'
  printf '3. Prepare a reviewed replacement for `%s` that keeps auth and replica-set identity intact while applying only the approved non-default settings.\n' "${configmap_name}"
  printf '4. If MongoDB user or role tightening is part of the change window, review the captured usersInfo/rolesInfo output and script those updates separately so privileges remain minimal and intentional.\n'
  printf '5. Roll one member at a time, waiting for PRIMARY/SECONDARY recovery after each restart before moving to the next pod.\n'
  printf '6. Re-run read-only checks for rs.status(), users/roles, log verbosity, and slowOpThresholdMs after the rollout, then archive the before/after evidence.\n'
  printf '7. Keep rollback assets prepared but do not execute them unless the approved change window fails.\n\n'

  printf '## Safety Notes\n'
  printf '%s\n' '- This artifact is review-only and must not mutate the live cluster.'
  printf '%s\n' '- Earlier workflow stages may rely on the current users, roles, replica count, and config values captured above.'
  printf '%s\n\n' '- Re-snapshot the StatefulSet and ConfigMap immediately before any real change window.'

  printf '## Replica Set Config Snapshot\n```json\n%s\n```\n\n' "$(cat "${tmp_dir}/rs-conf.json")"
  printf '## StatefulSet Snapshot\n```yaml\n%s\n```\n\n' "$(cat "${tmp_dir}/statefulset.yaml")"
  printf '## Service Snapshot\n```text\n%s\n```\n' "$(cat "${tmp_dir}/services.txt")"
} > "${plan_path}"

kubectl -n "${ns}" create configmap change-plan \
  --from-file=plan.md="${plan_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "prepared change-plan ConfigMap"
