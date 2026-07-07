#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: spark/spark_runtime_ops
# Strategy: native_shell
# Notes: Repairs the live runtime issues in place instead of reapplying the
# suspended Job manifests, which hits immutable Job template errors.

static_solver_export_namespace_if_unset "spark-runtime"

ns="${BENCH_NAMESPACE}"
config_name="${BENCH_PARAM_CONFIGMAP_NAME:-spark-config}"
secret_name="${BENCH_PARAM_SECRET_NAME:-spark-credentials}"
monitor_deployment="${BENCH_PARAM_MONITOR_DEPLOYMENT_NAME:-spark-monitor}"
batch_job="${BENCH_PARAM_JOB_NAME:-spark-batch-processor}"
data_processor_job="spark-data-processor"
executor_memory="${BENCH_PARAM_EXECUTOR_MEMORY:-512m}"
api_key="${BENCH_PARAM_API_KEY:-sk-valid-key-12345}"
monitor_image="busybox:1.36"

kubectl -n "${ns}" patch configmap "${config_name}" --type merge \
  -p "{\"data\":{\"spark.executor.memory\":\"${executor_memory}\"}}"

kubectl -n "${ns}" create secret generic "${secret_name}" \
  --from-literal="api-key=${api_key}" \
  --dry-run=client -o yaml | kubectl apply -f -

monitor_container="$(
  kubectl -n "${ns}" get deployment "${monitor_deployment}" \
    -o jsonpath='{.spec.template.spec.containers[0].name}'
)"
kubectl -n "${ns}" set image "deployment/${monitor_deployment}" \
  "${monitor_container}=${monitor_image}"

for job_name in "${batch_job}" "${data_processor_job}"; do
  kubectl -n "${ns}" patch job "${job_name}" --type merge \
    -p '{"spec":{"suspend":false}}'
done

kubectl -n "${ns}" rollout status "deployment/${monitor_deployment}" --timeout=300s
kubectl -n "${ns}" wait --for=condition=complete "job/${batch_job}" --timeout=300s
kubectl -n "${ns}" wait --for=condition=complete "job/${data_processor_job}" --timeout=300s

static_solver_wait_for_deployment_ready_replicas "spark-master" 1
static_solver_wait_for_deployment_ready_replicas "spark-worker" 1

static_solver_write_submit "completed Spark runtime repairs in place"
