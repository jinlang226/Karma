#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: spark/spark_multi_tenant
# Strategy: native_shell
# Notes: Fixes the known Team A RoleBinding bug, repairs the History Server PVC
# and log-dir settings, then recreates the tenant SparkPi jobs cleanly.

team_a_ns="${BENCH_NS_TEAM_A:-spark-team-a}"
team_b_ns="${BENCH_NS_TEAM_B:-spark-team-b}"
history_ns="${BENCH_NS_HISTORY:-spark-history}"
deployment_name="${BENCH_PARAM_DEPLOYMENT_NAME:-spark-history-server}"
pvc_name="${BENCH_PARAM_PVC_NAME:-spark-history-pvc}"
log_dir="${BENCH_PARAM_LOG_DIR:-/mnt/spark-logs}"

kubectl -n "${team_a_ns}" patch rolebinding spark-role-binding --type=json \
  -p='[{"op":"replace","path":"/subjects/0/namespace","value":"spark-team-a"}]'

kubectl -n "${history_ns}" patch deployment "${deployment_name}" --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/volumes/0/persistentVolumeClaim/claimName","value":"'"${pvc_name}"'"}]'

kubectl -n "${history_ns}" set env deployment/"${deployment_name}" \
  SPARK_HISTORY_OPTS="-Dspark.history.fs.logDirectory=${log_dir} -Dspark.history.fs.update.interval=5s -Dspark.history.ui.port=18080"

kubectl -n "${history_ns}" rollout status deployment/"${deployment_name}" --timeout=300s

kubectl -n "${team_a_ns}" delete job spark-pi-team-a --ignore-not-found=true
kubectl -n "${team_b_ns}" delete job spark-pi-team-b --ignore-not-found=true

kubectl apply -f "${STATIC_SOLVER_REPO_ROOT}/cases/spark/spark_multi_tenant/resource/spark-pi-job-team-a.yaml"
kubectl apply -f "${STATIC_SOLVER_REPO_ROOT}/cases/spark/spark_multi_tenant/resource/spark-pi-job-team-b.yaml"

kubectl -n "${team_a_ns}" wait --for=condition=complete job/spark-pi-team-a --timeout=300s
kubectl -n "${team_b_ns}" wait --for=condition=complete job/spark-pi-team-b --timeout=300s

static_solver_write_submit "completed tenant SparkPi jobs and repaired history server"
