#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: ray/upgrade_version
# Strategy: shell_wrapper_variant
# Imported reference: ray/version_upgrade
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_ray_version_upgrade.sh

static_solver_export_namespace_if_unset "ray"
cluster_prefix="${BENCH_PARAM_CLUSTER_PREFIX:-ray}"
target_image="${BENCH_PARAM_TO_IMAGE:-rayproject/ray:2.9.0}"
worker_replicas="$(
  kubectl -n "${BENCH_NAMESPACE}" get deployment "${cluster_prefix}-worker" \
    -o jsonpath='{.spec.replicas}'
)"
expected_nodes="$((worker_replicas + 1))"

kubectl -n "${BENCH_NAMESPACE}" set image \
  "deployment/${cluster_prefix}-head" "ray-head=${target_image}"
kubectl -n "${BENCH_NAMESPACE}" set image \
  "deployment/${cluster_prefix}-worker" "ray-worker=${target_image}"
kubectl -n "${BENCH_NAMESPACE}" rollout status "deployment/${cluster_prefix}-head" --timeout=300s
kubectl -n "${BENCH_NAMESPACE}" rollout status "deployment/${cluster_prefix}-worker" --timeout=300s
static_solver_wait_for_ray_nodes "${expected_nodes}" "${cluster_prefix}"
static_solver_wait_for_deployment_ready_replicas "${cluster_prefix}-head" 1
static_solver_wait_for_deployment_ready_replicas "${cluster_prefix}-worker" "${worker_replicas}"

static_solver_write_submit "upgraded Ray cluster"
