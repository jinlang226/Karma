#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/change-plan-only
# Strategy: state_capture_shell
# Notes: Produce a review-only change plan as a ConfigMap without mutating the
# live cluster.

static_solver_export_namespace_if_unset "cockroachdb"

prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
plan_path="${STATIC_SOLVER_STAGE_DIR}/plan.md"

cluster_flag="--insecure"
if kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
  ls /cockroach/cockroach-certs/ca.crt >/dev/null 2>&1; then
  cluster_flag="--certs-dir=/cockroach/cockroach-certs"
fi

version_info="$(
  kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
    ./cockroach version 2>&1 || true
)"

non_default_settings="$(
  kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
    ./cockroach sql "${cluster_flag}" --format=tsv \
    -e "SELECT variable, value FROM crdb_internal.cluster_settings WHERE value != default_value;" \
    2>&1 || true
)"

zone_configs="$(
  kubectl -n "${BENCH_NAMESPACE}" exec "${prefix}-0" -- \
    ./cockroach sql "${cluster_flag}" --format=tsv \
    -e "SELECT target, raw_config_sql FROM crdb_internal.zones;" \
    2>&1 || true
)"

statefulset_info="$(
  kubectl -n "${BENCH_NAMESPACE}" get statefulset "${prefix}" -o yaml 2>&1 || true
)"

pod_overview="$(
  kubectl -n "${BENCH_NAMESPACE}" get pods -o wide 2>&1 || true
)"

{
  printf '# CockroachDB Change Plan\n\n'
  printf '## Scope\n'
  printf '%s\n' "- Namespace: \`${BENCH_NAMESPACE}\`"
  printf '%s\n' "- Cluster prefix: \`${prefix}\`"
  printf '%s\n\n' '- Objective: prepare the next minor-version upgrade plan without applying changes.'

  printf '## Current Version Snapshot\n```text\n%s\n```\n\n' "${version_info}"
  printf '## Current Non-Default Cluster Settings\n```text\n%s\n```\n\n' "${non_default_settings}"
  printf '## Current Zone Configuration\n```text\n%s\n```\n\n' "${zone_configs}"
  printf '## StatefulSet Snapshot\n```yaml\n%s\n```\n\n' "${statefulset_info}"
  printf '## Pod Snapshot\n```text\n%s\n```\n\n' "${pod_overview}"

  printf '## Proposed Change Steps\n'
  printf '1. Validate cluster health, node readiness, and replica quorum.\n'
  printf '2. Confirm the target minor version image and release notes for compatibility.\n'
  printf '3. Review the non-default settings and zone overrides above for compatibility with the target version.\n'
  printf '4. Roll the StatefulSet image update one partition at a time and verify readiness after each pod.\n'
  printf '5. Re-check cluster settings, zone config, and client connectivity after rollout.\n'
  printf '6. Keep rollback assets and audit artifacts ready before execution.\n\n'

  printf '## Safety Checks\n'
  printf '%s\n' '- Do not apply this plan directly from the review artifact.'
  printf '%s\n' '- Reconfirm secure vs insecure SQL connectivity before execution.'
  printf '%s\n\n' '- Re-snapshot settings and version immediately before the maintenance window.'

  printf '## Rollback Notes\n'
  printf '%s\n' '- Preserve the current image digest before upgrade.'
  printf '%s\n' '- Preserve the current non-default settings and zone config outputs above.'
  printf '%s\n' '- If rollout fails, stop after the current partition and validate cluster health before any rollback action.'
} > "${plan_path}"

kubectl -n "${BENCH_NAMESPACE}" create configmap change-plan \
  --from-file=plan.md="${plan_path}" \
  --dry-run=client -o yaml | kubectl apply -f -

static_solver_write_submit "prepared change-plan ConfigMap"
