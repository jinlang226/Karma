#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: rabbitmq/rollback-rehearsal
# Strategy: native_shell
# Notes: Read-only rollback planner. It captures the current RabbitMQ state and
# writes a review-only rollback script to ConfigMap/rollback-rehearsal without
# executing any rollback action.

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
capture_rabbitmqctl cluster_status.txt cluster_status
capture_rabbitmqctl vhosts.txt list_vhosts
capture_rabbitmqctl users.txt list_users
capture_rabbitmqctl permissions.txt list_permissions -p /app
capture_rabbitmqctl policies.txt list_policies -p /app
capture_rabbitmqctl queues.txt list_queues -p /app name type durable arguments messages messages_ready messages_unacknowledged

script_path="${STATIC_SOLVER_STAGE_DIR}/rollback.sh"
{
  cat <<EOF
#!/usr/bin/env bash
set -euo pipefail

ns="${ns}"
prefix="${prefix}"
pod="${pod}"

# Review-only rollback rehearsal for RabbitMQ.
# Generated from the live cluster snapshot below. Review every command before
# use in a real maintenance window. Do not execute blindly.

# Snapshot the broker before any rollback action.
kubectl -n "\${ns}" exec "\${pod}" -- rabbitmqctl cluster_status
kubectl -n "\${ns}" exec "\${pod}" -- rabbitmqctl list_vhosts
kubectl -n "\${ns}" exec "\${pod}" -- rabbitmqctl list_users
kubectl -n "\${ns}" exec "\${pod}" -- rabbitmqctl list_permissions -p /app
kubectl -n "\${ns}" exec "\${pod}" -- rabbitmqctl list_policies -p /app
kubectl -n "\${ns}" get statefulset "\${prefix}" -o yaml

# Candidate rollback actions for review:
# 1. Re-apply the previously approved ConfigMap / Secret versions if a config
#    change or TLS rotation introduced the regression.
# 2. Clear only the non-default /app policies that were added during the failed
#    change window, then re-check queue health before touching permissions.
# 3. Revert any user / permission drift only after confirming which clients
#    still depend on the current access model.
# 4. Roll brokers one at a time only if config or TLS rollback requires a
#    restart, waiting for quorum after each pod.

# Example commands to review and tailor before use:
# kubectl -n "\${ns}" exec "\${pod}" -- rabbitmqctl clear_policy -p /app <policy-name>
# kubectl -n "\${ns}" exec "\${pod}" -- rabbitmqctl delete_user <temporary-user>
# kubectl -n "\${ns}" exec "\${pod}" -- rabbitmqctl delete_vhost /app
# kubectl -n "\${ns}" rollout restart statefulset/"\${prefix}"
# kubectl -n "\${ns}" rollout status statefulset/"\${prefix}" --timeout=600s

# Captured evidence from the generation point:
EOF

  while IFS= read -r line; do
    printf '# %s\n' "${line}"
  done < "${tmp_dir}/cluster_status.txt"
  while IFS= read -r line; do
    printf '# %s\n' "${line}"
  done < "${tmp_dir}/vhosts.txt"
  while IFS= read -r line; do
    printf '# %s\n' "${line}"
  done < "${tmp_dir}/users.txt"
  while IFS= read -r line; do
    printf '# %s\n' "${line}"
  done < "${tmp_dir}/permissions.txt"
  while IFS= read -r line; do
    printf '# %s\n' "${line}"
  done < "${tmp_dir}/policies.txt"
  while IFS= read -r line; do
    printf '# %s\n' "${line}"
  done < "${tmp_dir}/queues.txt"

  cat <<EOF

# Current rabbitmq.conf snapshot for operator review:
cat <<'EOF_RABBITMQ_CONF'
$(cat "${tmp_dir}/config.txt")
EOF_RABBITMQ_CONF
EOF
} > "${script_path}"

kubectl -n "${ns}" create configmap rollback-rehearsal \
  --from-file=rollback.sh="${script_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "prepared rollback-rehearsal ConfigMap"
