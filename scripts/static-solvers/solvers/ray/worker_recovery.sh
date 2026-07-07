#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: ray/worker_recovery
# Strategy: native_shell
# Notes: Replaces the crash-looping worker command with the known-good startup
# command, preserves the inherited worker replica count, and waits for full Ray
# cluster membership to recover.

static_solver_export_namespace_if_unset "ray"

ns="${BENCH_NAMESPACE}"
cluster_prefix="${BENCH_PARAM_CLUSTER_PREFIX:-ray}"
head_deployment="${BENCH_PARAM_HEAD_DEPLOYMENT_NAME:-${cluster_prefix}-head}"
worker_deployment="${BENCH_PARAM_WORKER_DEPLOYMENT_NAME:-${cluster_prefix}-worker}"
head_service="${BENCH_PARAM_HEAD_SERVICE_NAME:-${cluster_prefix}-head}"
head_port="${BENCH_PARAM_HEAD_SERVICE_PORT:-6379}"
worker_cpus="${BENCH_PARAM_WORKER_CPUS:-1}"
worker_extra_args="${BENCH_PARAM_WORKER_START_FLAGS:-}"

worker_replicas="${BENCH_PARAM_EXPECTED_WORKERS:-${BENCH_PARAM_WORKER_REPLICAS:-}}"
if [[ -z "${worker_replicas}" ]]; then
  worker_replicas="$(
    kubectl -n "${ns}" get deployment "${worker_deployment}" -o jsonpath='{.spec.replicas}'
  )"
fi
worker_replicas="${worker_replicas:-2}"
expected_nodes=$((worker_replicas + 1))

patched_command_json="$(
  python3 - "${head_service}" "${head_port}" "${worker_cpus}" "${worker_extra_args}" <<'PY'
import json
import sys

head_service, head_port, worker_cpus, extra_args = sys.argv[1:]
command = (
    f'ray start --address={head_service}:{head_port} '
    f'--node-ip-address="${{MY_POD_IP}}" --num-cpus={worker_cpus}'
)
if extra_args.strip():
    command += f" {extra_args.strip()}"
command += " --block"
print(json.dumps(["sh", "-c", command]), end="")
PY
)"

kubectl -n "${ns}" patch deployment "${worker_deployment}" --type=json \
  -p="[{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/command\",\"value\":${patched_command_json}}]"

kubectl -n "${ns}" scale "deployment/${worker_deployment}" --replicas="${worker_replicas}" >/dev/null
kubectl -n "${ns}" rollout status "deployment/${head_deployment}" --timeout=300s
kubectl -n "${ns}" rollout status "deployment/${worker_deployment}" --timeout=300s

if ! static_solver_wait_for_ray_nodes "${expected_nodes}" "${cluster_prefix}" 60; then
  static_solver_log "restarting Ray head and worker deployments to recover cluster membership"
  kubectl -n "${ns}" rollout restart "deployment/${head_deployment}"
  kubectl -n "${ns}" rollout status "deployment/${head_deployment}" --timeout=300s
  kubectl -n "${ns}" rollout restart "deployment/${worker_deployment}"
  kubectl -n "${ns}" rollout status "deployment/${worker_deployment}" --timeout=300s
  static_solver_wait_for_ray_nodes "${expected_nodes}" "${cluster_prefix}" 180
fi

static_solver_wait_for_deployment_ready_replicas "${head_deployment}" 1
static_solver_wait_for_deployment_ready_replicas "${worker_deployment}" "${worker_replicas}"

static_solver_write_submit "recovered Ray worker startup and cluster membership"
