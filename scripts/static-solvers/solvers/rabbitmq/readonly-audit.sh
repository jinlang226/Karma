#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: rabbitmq/readonly-audit
# Strategy: native_shell
# Notes: Read-only auditor. It captures the live RabbitMQ configuration and
# writes findings to ConfigMap/config-audit without modifying cluster state.

static_solver_export_namespace_if_unset "rabbitmq"

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-rabbitmq}"
pod="${prefix}-0"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

capture_cmd() {
  local name="${1:?output name is required}"
  shift
  "$@" > "${tmp_dir}/${name}" 2>&1 || true
}

capture_rabbitmqctl() {
  local name="${1:?output name is required}"
  shift
  capture_cmd "${name}" kubectl -n "${ns}" exec "${pod}" -- rabbitmqctl "$@"
}

capture_cmd pods.txt kubectl -n "${ns}" get pods -o wide
capture_cmd services.txt kubectl -n "${ns}" get svc
capture_cmd secrets.txt kubectl -n "${ns}" get secret
capture_cmd statefulset.yaml kubectl -n "${ns}" get statefulset "${prefix}" -o yaml
capture_cmd config.txt kubectl -n "${ns}" get configmap "${prefix}-config" -o "jsonpath={.data.rabbitmq\\.conf}"
capture_cmd plugins.txt kubectl -n "${ns}" get configmap "${prefix}-config" -o "jsonpath={.data.enabled_plugins}"
capture_rabbitmqctl status.txt status
capture_rabbitmqctl cluster_status.txt cluster_status
capture_rabbitmqctl vhosts.txt list_vhosts
capture_rabbitmqctl users.txt list_users
capture_rabbitmqctl permissions.txt list_permissions -p /app
capture_rabbitmqctl policies.txt list_policies -p /app
capture_rabbitmqctl queues.txt list_queues -p /app name type durable arguments messages messages_ready messages_unacknowledged

report_path="${STATIC_SOLVER_STAGE_DIR}/findings.txt"
{
  printf 'RabbitMQ Read-Only Audit\n'
  printf 'Namespace: %s\n' "${ns}"
  printf 'Cluster prefix: %s\n\n' "${prefix}"

  printf '=== Broker Status ===\n%s\n\n' "$(cat "${tmp_dir}/status.txt")"
  printf '=== Cluster Status ===\n%s\n\n' "$(cat "${tmp_dir}/cluster_status.txt")"
  printf '=== Pods ===\n%s\n\n' "$(cat "${tmp_dir}/pods.txt")"
  printf '=== Services ===\n%s\n\n' "$(cat "${tmp_dir}/services.txt")"
  printf '=== Secret Inventory ===\n%s\n\n' "$(cat "${tmp_dir}/secrets.txt")"
  printf '=== Vhosts ===\n%s\n\n' "$(cat "${tmp_dir}/vhosts.txt")"
  printf '=== Users ===\n%s\n\n' "$(cat "${tmp_dir}/users.txt")"
  printf '=== /app Permissions ===\n%s\n\n' "$(cat "${tmp_dir}/permissions.txt")"
  printf '=== /app Policies ===\n%s\n\n' "$(cat "${tmp_dir}/policies.txt")"
  printf '=== /app Queues ===\n%s\n\n' "$(cat "${tmp_dir}/queues.txt")"
  printf '=== Enabled Plugins ===\n%s\n\n' "$(cat "${tmp_dir}/plugins.txt")"
  printf '=== rabbitmq.conf ===\n%s\n\n' "$(cat "${tmp_dir}/config.txt")"
  printf '=== StatefulSet Snapshot ===\n%s\n\n' "$(cat "${tmp_dir}/statefulset.yaml")"

  printf '=== Audit Notes ===\n'
  printf '%s\n' '- This report is read-only; no remediation was applied.'
  printf '%s\n' '- Review queue arguments and /app permissions carefully before any follow-up change.'
  printf '%s\n' '- If TLS or monitoring is expected, confirm the enabled plugins and config align with the intended operating model.'
} > "${report_path}"

kubectl -n "${ns}" create configmap config-audit \
  --from-file=findings.txt="${report_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "prepared config-audit ConfigMap"
