#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/monitoring-integration
# Strategy: native_shell
# Notes: Wires a ServiceMonitor that matches the inherited cluster mode and
# waits until Prometheus is actively scraping the CockroachDB metrics target.

static_solver_export_namespace_if_unset "cockroachdb"
static_solver_export_cockroachdb_defaults

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX}"
metrics_path="${BENCH_PARAM_METRICS_PATH:-/_status/vars}"
monitor_name="crdb-monitor"
monitoring_ns="${BENCH_NS_MONITORING:-monitoring}"
prometheus_service_name="${BENCH_PARAM_PROMETHEUS_SERVICE_NAME:-prometheus}"
scheme="http"
tls_block=""
resource_dir="${STATIC_SOLVER_REPO_ROOT}/cases/cockroachdb/monitoring-integration/resource"
oracle_path="${STATIC_SOLVER_REPO_ROOT}/cases/cockroachdb/monitoring-integration/oracle/oracle.py"

export BENCH_NS_MONITORING="${monitoring_ns}"

ensure_monitoring_stack() {
  if kubectl -n "${monitoring_ns}" get service "${prometheus_service_name}" >/dev/null 2>&1 &&
    kubectl -n "${monitoring_ns}" wait --for=condition=ready pod -l prometheus=crdb --timeout=3s >/dev/null 2>&1; then
    return 0
  fi

  kubectl create namespace "${monitoring_ns}" --dry-run=client -o yaml | kubectl apply -f -
  kubectl apply -f "${resource_dir}/monitoring-crds.yaml"
  kubectl -n "${monitoring_ns}" apply -f "${resource_dir}/prometheus-operator.yaml"
  kubectl -n "${monitoring_ns}" apply -f "${resource_dir}/prometheus-rbac.yaml"
  kubectl -n "${monitoring_ns}" apply -f "${resource_dir}/prometheus.yaml"
  kubectl -n "${monitoring_ns}" wait --for=condition=ready pod -l app=prometheus-operator --timeout=300s

  for _ in $(seq 1 60); do
    pod_count="$(
      kubectl -n "${monitoring_ns}" get pod -l prometheus=crdb --no-headers 2>/dev/null | \
        wc -l | tr -d ' '
    )"
    if [[ "${pod_count}" -ge 1 ]]; then
      kubectl -n "${monitoring_ns}" wait --for=condition=ready pod -l prometheus=crdb --timeout=300s
      return 0
    fi
    sleep 2
  done

  static_solver_fail "Prometheus pod was not created in namespace ${monitoring_ns}"
}

ensure_curl_test() {
  if ! kubectl -n "${ns}" get pod curl-test >/dev/null 2>&1; then
    kubectl -n "${ns}" apply -f "${resource_dir}/curl-test.yaml"
  fi
  kubectl -n "${ns}" wait --for=condition=Ready pod/curl-test --timeout=120s
}

if kubectl -n "${ns}" exec "${prefix}-0" -- ls /cockroach/cockroach-certs/ca.crt >/dev/null 2>&1; then
  scheme="https"
  tls_block=$'    tlsConfig:\n      insecureSkipVerify: true'
fi

ensure_monitoring_stack
ensure_curl_test

cat <<EOF | kubectl -n "${ns}" apply -f -
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: ${monitor_name}
spec:
  namespaceSelector:
    matchNames:
    - ${ns}
  selector:
    matchLabels:
      app.kubernetes.io/name: cockroachdb
      app.kubernetes.io/instance: ${prefix}
  endpoints:
  - port: http
    path: ${metrics_path}
    interval: 5s
    scheme: ${scheme}
${tls_block}
EOF

for _ in $(seq 1 36); do
  if python3 "${oracle_path}" >/dev/null 2>&1; then
    static_solver_write_submit "configured CockroachDB monitoring integration"
    exit 0
  fi
  sleep 5
done

if python3 "${oracle_path}"; then
  static_solver_write_submit "configured CockroachDB monitoring integration"
  exit 0
fi

static_solver_fail "Prometheus never reported an active CockroachDB metrics target"
