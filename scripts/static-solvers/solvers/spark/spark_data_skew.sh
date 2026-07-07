#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: spark/spark_data_skew
# Strategy: native_shell
# Notes: Re-applies the dedicated spark-skew namespace resources additively,
# reruns the baseline job cleanly, and then applies a workflow-configurable
# optimization job so the oracle sees at least one successful skew mitigation.

static_solver_export_namespace_if_unset "spark-skew"

ns="${BENCH_NAMESPACE}"
case_root="${STATIC_SOLVER_REPO_ROOT}/cases/spark/spark_data_skew/resource"
master_deployment="spark-master"
worker_deployment="spark-worker"
baseline_job="spark-skew-baseline"
broadcast_job="spark-skew-broadcast"
aqe_job="spark-skew-aqe"
optimization_strategy="${BENCH_PARAM_OPTIMIZATION_STRATEGY:-broadcast}"
spark_image="${BENCH_PARAM_SPARK_IMAGE:-}"

if [[ -z "${spark_image}" ]]; then
  spark_image="$(
    kubectl -n "${ns}" get deployment "${master_deployment}" \
      -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true
  )"
fi
spark_image="${spark_image:-apache/spark:3.5.3}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

render_manifest() {
  local source_path="${1:?source path is required}"
  local output_path="${2:?output path is required}"
  local name_override="${3:-}"
  python3 - "${source_path}" "${output_path}" "${ns}" "${spark_image}" "${name_override}" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

import yaml

source_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
namespace = sys.argv[3]
spark_image = sys.argv[4]
name_override = sys.argv[5]

documents = list(yaml.safe_load_all(source_path.read_text()))
for document in documents:
    if not document:
        continue
    kind = document.get("kind")
    metadata = document.setdefault("metadata", {})
    if kind == "Namespace":
        metadata["name"] = namespace
        metadata.pop("namespace", None)
    else:
        metadata["namespace"] = namespace
    if kind == "Job" and name_override:
        metadata["name"] = name_override
    if kind == "RoleBinding":
        for subject in document.get("subjects") or []:
            if subject.get("kind") == "ServiceAccount":
                subject["namespace"] = namespace
    if kind in {"Deployment", "Job"}:
        pod_spec = (((document.get("spec") or {}).get("template") or {}).get("spec") or {})
        for container in pod_spec.get("containers") or []:
            if container.get("image"):
                container["image"] = spark_image

output_path.write_text(yaml.safe_dump_all(documents, sort_keys=False))
PY
}

rendered_rbac="${tmp_dir}/rbac.yaml"
rendered_configmap="${tmp_dir}/scripts-configmap.yaml"
rendered_cluster="${tmp_dir}/spark-cluster.yaml"
rendered_baseline_job="${tmp_dir}/skew-job.yaml"
rendered_broadcast_job="${tmp_dir}/skew-job-broadcast.yaml"
rendered_aqe_job="${tmp_dir}/skew-job-aqe.yaml"

render_manifest "${case_root}/rbac.yaml" "${rendered_rbac}"
render_manifest "${case_root}/scripts-configmap.yaml" "${rendered_configmap}"
render_manifest "${case_root}/spark-cluster.yaml" "${rendered_cluster}"
render_manifest "${case_root}/skew-job.yaml" "${rendered_baseline_job}" "${baseline_job}"
render_manifest "${case_root}/skew-job-broadcast.yaml" "${rendered_broadcast_job}" "${broadcast_job}"
render_manifest "${case_root}/skew-job-aqe.yaml" "${rendered_aqe_job}" "${aqe_job}"

kubectl create namespace "${ns}" --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "${rendered_rbac}"
kubectl apply -f "${rendered_configmap}"
kubectl apply -f "${rendered_cluster}"

kubectl -n "${ns}" rollout status "deployment/${master_deployment}" --timeout=300s
kubectl -n "${ns}" rollout status "deployment/${worker_deployment}" --timeout=300s
static_solver_wait_for_deployment_ready_replicas "${master_deployment}" 1
static_solver_wait_for_deployment_ready_replicas "${worker_deployment}" 1

run_job_cleanly() {
  local manifest_path="${1:?manifest path is required}"
  local job_name="${2:?job name is required}"
  kubectl -n "${ns}" delete job "${job_name}" --ignore-not-found=true --wait=true
  kubectl apply -f "${manifest_path}"
  kubectl -n "${ns}" wait --for=condition=complete "job/${job_name}" --timeout=300s
}

run_job_cleanly "${rendered_baseline_job}" "${baseline_job}"

case "${optimization_strategy}" in
  broadcast)
    run_job_cleanly "${rendered_broadcast_job}" "${broadcast_job}"
    ;;
  aqe)
    run_job_cleanly "${rendered_aqe_job}" "${aqe_job}"
    ;;
  both)
    run_job_cleanly "${rendered_broadcast_job}" "${broadcast_job}"
    run_job_cleanly "${rendered_aqe_job}" "${aqe_job}"
    ;;
  *)
    static_solver_fail "unsupported optimization strategy: ${optimization_strategy}"
    ;;
esac

static_solver_write_submit "completed Spark skew baseline and optimization jobs"
