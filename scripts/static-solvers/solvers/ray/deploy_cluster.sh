#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: ray/deploy_cluster
# Strategy: shell_wrapper_variant
# Imported reference: ray/cluster_ready
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_ray_cluster_ready.sh

static_solver_export_namespace_if_unset "ray"
static_solver_ensure_vendor_resources_link

cluster_prefix="${BENCH_PARAM_CLUSTER_PREFIX:-ray}"
ray_image="${BENCH_PARAM_RAY_IMAGE:-rayproject/ray:2.9.0}"
worker_replicas="${BENCH_PARAM_WORKER_REPLICAS:-2}"
expected_nodes="$((worker_replicas + 1))"

sed -e "s/__CLUSTER_PREFIX__/${cluster_prefix}/g" \
  "${STATIC_SOLVER_STAGE_DIR}/resources/ray/cluster_ready/resource/ray-head-service.yaml" | \
  kubectl -n "${BENCH_NAMESPACE}" apply -f -
sed -e "s/__CLUSTER_PREFIX__/${cluster_prefix}/g" -e "s#__RAY_IMAGE__#${ray_image}#g" \
  "${STATIC_SOLVER_STAGE_DIR}/resources/ray/cluster_ready/resource/ray-head.yaml" | \
  kubectl -n "${BENCH_NAMESPACE}" apply -f -
sed -e "s/__CLUSTER_PREFIX__/${cluster_prefix}/g" \
  -e "s#__RAY_IMAGE__#${ray_image}#g" \
  -e "s/__WORKER_REPLICAS__/${worker_replicas}/g" \
  "${STATIC_SOLVER_STAGE_DIR}/resources/ray/cluster_ready/resource/ray-worker.yaml" | \
  kubectl -n "${BENCH_NAMESPACE}" apply -f -

kubectl -n "${BENCH_NAMESPACE}" rollout status "deployment/${cluster_prefix}-head" --timeout=300s
kubectl -n "${BENCH_NAMESPACE}" rollout status "deployment/${cluster_prefix}-worker" --timeout=300s

if ! static_solver_wait_for_ray_nodes "${expected_nodes}" "${cluster_prefix}" 60; then
  static_solver_log "restarting Ray head and worker deployments to recover incomplete cluster registration"
  kubectl -n "${BENCH_NAMESPACE}" rollout restart "deployment/${cluster_prefix}-head"
  kubectl -n "${BENCH_NAMESPACE}" rollout status "deployment/${cluster_prefix}-head" --timeout=300s
  kubectl -n "${BENCH_NAMESPACE}" rollout restart "deployment/${cluster_prefix}-worker"
  kubectl -n "${BENCH_NAMESPACE}" rollout status "deployment/${cluster_prefix}-worker" --timeout=300s
  static_solver_wait_for_ray_nodes "${expected_nodes}" "${cluster_prefix}" 180
fi

static_solver_wait_for_deployment_ready_replicas "${cluster_prefix}-head" 1
static_solver_wait_for_deployment_ready_replicas "${cluster_prefix}-worker" "${worker_replicas}"

static_solver_write_submit "converged Ray cluster"
