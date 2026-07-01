#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: ray/scale_workers
# Strategy: shell_wrapper_variant
# Imported reference: ray/worker_scaling
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_ray_worker_scaling.sh

static_solver_export_namespace_if_unset "ray"
cluster_prefix="${BENCH_PARAM_CLUSTER_PREFIX:-ray}"
target_replicas="${BENCH_PARAM_TARGET_WORKER_REPLICAS:-3}"
expected_nodes="$((target_replicas + 1))"

kubectl -n "${BENCH_NAMESPACE}" scale "deployment/${cluster_prefix}-worker" --replicas="${target_replicas}"
kubectl -n "${BENCH_NAMESPACE}" rollout status "deployment/${cluster_prefix}-worker" --timeout=300s

if ! static_solver_wait_for_ray_nodes "${expected_nodes}" "${cluster_prefix}" 60; then
  static_solver_log "restarting Ray worker deployment to recover stale worker membership"
  kubectl -n "${BENCH_NAMESPACE}" rollout restart "deployment/${cluster_prefix}-worker"
  kubectl -n "${BENCH_NAMESPACE}" rollout status "deployment/${cluster_prefix}-worker" --timeout=300s
  static_solver_wait_for_ray_nodes "${expected_nodes}" "${cluster_prefix}" 120
fi

static_solver_wait_for_deployment_ready_replicas "${cluster_prefix}-worker" "${target_replicas}"

static_solver_write_submit "scaled Ray workers"
