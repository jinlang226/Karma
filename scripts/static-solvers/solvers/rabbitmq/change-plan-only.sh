#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: rabbitmq/change-plan-only
# Strategy: native_shell
# Notes: Read-only planner. It inspects the live RabbitMQ cluster and writes a
# review-only migration plan to ConfigMap/change-plan without mutating broker
# state.

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
capture_cmd statefulset.yaml kubectl -n "${ns}" get statefulset "${prefix}" -o yaml
capture_cmd config.txt kubectl -n "${ns}" get configmap "${prefix}-config" -o "jsonpath={.data.rabbitmq\\.conf}"
capture_cmd plugins.txt kubectl -n "${ns}" get configmap "${prefix}-config" -o "jsonpath={.data.enabled_plugins}"
capture_cmd secret_list.txt kubectl -n "${ns}" get secret
capture_rabbitmqctl version.txt version
capture_rabbitmqctl status.txt status
capture_rabbitmqctl cluster_status.txt cluster_status
capture_rabbitmqctl vhosts.txt list_vhosts
capture_rabbitmqctl users.txt list_users
capture_rabbitmqctl permissions.txt list_permissions -p /app
capture_rabbitmqctl policies.txt list_policies -p /app
capture_rabbitmqctl queues.txt list_queues -p /app name type durable arguments messages messages_ready messages_unacknowledged

plan_path="${STATIC_SOLVER_STAGE_DIR}/plan.md"
{
  printf '# RabbitMQ Change / Migration Plan\n\n'
  printf '## Scope\n'
  printf '%s\n' "- Namespace: \`${ns}\`"
  printf '%s\n' "- Cluster prefix: \`${prefix}\`"
  printf '%s\n\n' '- Objective: prepare a review-only plan for the next upgrade / config migration without applying it.'

  printf '## Current State Summary\n'
  printf '%s\n' '- RabbitMQ version snapshot:'
  printf '```text\n%s\n```\n\n' "$(cat "${tmp_dir}/version.txt")"
  printf '%s\n' '- Live broker / cluster status:'
  printf '```text\n%s\n```\n\n' "$(cat "${tmp_dir}/cluster_status.txt")"
  printf '%s\n' '- Current vhosts, users, permissions, policies, and queues:'
  printf '```text\n%s\n\n%s\n\n%s\n\n%s\n\n%s\n```\n\n' \
    "$(cat "${tmp_dir}/vhosts.txt")" \
    "$(cat "${tmp_dir}/users.txt")" \
    "$(cat "${tmp_dir}/permissions.txt")" \
    "$(cat "${tmp_dir}/policies.txt")" \
    "$(cat "${tmp_dir}/queues.txt")"

  printf '## Kubernetes Snapshot\n'
  printf '### Pods\n```text\n%s\n```\n\n' "$(cat "${tmp_dir}/pods.txt")"
  printf '### Services\n```text\n%s\n```\n\n' "$(cat "${tmp_dir}/services.txt")"
  printf '### Enabled Plugins\n```text\n%s\n```\n\n' "$(cat "${tmp_dir}/plugins.txt")"
  printf '### rabbitmq.conf\n```ini\n%s\n```\n\n' "$(cat "${tmp_dir}/config.txt")"

  printf '## Proposed Change Window Steps\n'
  printf '1. Re-confirm the 3-broker cluster health and queue state shown above immediately before the maintenance window.\n'
  printf '2. Snapshot the current StatefulSet, ConfigMap, Secret list, and broker metadata so rollback inputs match the exact live state.\n'
  printf '3. Review non-default vhosts, users, permissions, and policies before any upgrade so the post-change validation matches the current contract.\n'
  printf '4. If TLS is enabled in the captured config, preserve the current leaf and CA materials before changing any cert or broker image.\n'
  printf '5. Roll one broker at a time, wait for quorum and queue health after each step, then re-check policies and permissions.\n'
  printf '6. Re-run app-client / monitoring smoke checks after the change and compare queue declarations against the captured baseline.\n'
  printf '7. Keep rollback assets prepared but do not execute them unless the scheduled window fails and operators approve rollback.\n\n'

  printf '## Safety Notes\n'
  printf '%s\n' '- This artifact is review-only and must not mutate the running cluster.'
  printf '%s\n' '- Earlier workflow stages may rely on the exact users, vhosts, queue arguments, and TLS state captured above.'
  printf '%s\n\n' '- Any real change should re-capture the same evidence immediately before execution.'

  printf '## Captured StatefulSet\n```yaml\n%s\n```\n\n' "$(cat "${tmp_dir}/statefulset.yaml")"
  printf '## Secret Inventory\n```text\n%s\n```\n' "$(cat "${tmp_dir}/secret_list.txt")"
} > "${plan_path}"

kubectl -n "${ns}" create configmap change-plan \
  --from-file=plan.md="${plan_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "prepared change-plan ConfigMap"
