#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: nginx-ingress/class_only_upgrade
# Strategy: native_shell
# Notes: this case is frequently chained after unrelated nginx stages whose
# shared curl-test probe causes the destructive standalone setup to be skipped.
# Re-create only the class-routing fixtures additively so the stage works both
# standalone and on inherited workflows.

static_solver_export_nginx_defaults "class-demo" "class-ingress-nginx"

app_ns="${BENCH_NS_APP}"
primary_ingress_ns="${BENCH_NS_INGRESS}"
secondary_ingress_ns="${BENCH_NS_INGRESS_2:-class-ingress-nginx-2}"
curl_pod_name="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
host="${BENCH_PARAM_HOST:-class.example.com}"
expected_body="${BENCH_PARAM_EXPECTED_BODY:-hello}"
ingress_name="${BENCH_PARAM_INGRESS_NAME:-demo-app}"
ingress_class_name="${BENCH_PARAM_INGRESS_CLASS_NAME:-ingress-2}"
service_name="${BENCH_PARAM_SERVICE_NAME:-demo-app}"

case_root="${STATIC_SOLVER_REPO_ROOT}/cases/nginx-ingress/class_only_upgrade"
resource_dir="${case_root}/resource"
decoy_dir="${case_root}/decoy"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

ensure_secondary_controller() {
  kubectl create namespace "${secondary_ingress_ns}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl apply -f "${resource_dir}/ingress-classes.yaml" >/dev/null
  kubectl apply -f "${resource_dir}/controller-2.yaml" >/dev/null
  kubectl -n "${secondary_ingress_ns}" rollout status deploy/ingress-nginx-controller --timeout=180s >/dev/null
}

ensure_demo_fixtures() {
  kubectl create namespace "${app_ns}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

  if ! kubectl -n "${app_ns}" get deploy "${service_name}" >/dev/null 2>&1 || ! kubectl -n "${app_ns}" get svc "${service_name}" >/dev/null 2>&1; then
    kubectl apply -f "${resource_dir}/demo-app.yaml" >/dev/null
  fi
  kubectl -n "${app_ns}" rollout status "deploy/${service_name}" --timeout=120s >/dev/null

  kubectl apply -f "${resource_dir}/ingress-gateway.yaml" >/dev/null

  cat > "${tmp_dir}/demo-ingress.yaml" <<EOF
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${ingress_name}
  namespace: ${app_ns}
spec:
  ingressClassName: ${ingress_class_name}
  rules:
    - host: ${host}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ${service_name}
                port:
                  number: 80
EOF
  kubectl apply -f "${tmp_dir}/demo-ingress.yaml" >/dev/null

  if [[ -f "${decoy_dir}/demo-app-alt.yaml" ]]; then
    kubectl apply -f "${decoy_dir}/demo-app-alt.yaml" >/dev/null
  fi
}

ensure_curl_test() {
  if ! kubectl -n "${app_ns}" get pod "${curl_pod_name}" >/dev/null 2>&1; then
    kubectl apply -f "${resource_dir}/curl-test.yaml" >/dev/null
  fi
  kubectl -n "${app_ns}" wait --for=condition=Ready "pod/${curl_pod_name}" --timeout=180s >/dev/null
}

wait_for_route() {
  local deadline body
  deadline=$((SECONDS + 120))
  while (( SECONDS < deadline )); do
    body="$(
      kubectl -n "${app_ns}" exec "${curl_pod_name}" -- \
        curl -sS -H "Host: ${host}" "http://ingress-gateway.${app_ns}.svc.cluster.local/" 2>/dev/null || true
    )"
    body="${body//$'\r'/}"
    body="${body//$'\n'/}"
    if [[ "${body}" == "${expected_body}" ]]; then
      return 0
    fi
    sleep 3
  done
  static_solver_fail "class ingress route did not become ready for host ${host}"
}

ensure_secondary_controller
ensure_demo_fixtures
ensure_curl_test
wait_for_route

static_solver_write_submit "configured explicit ingress-class routing via ingress-nginx-2"
