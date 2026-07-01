#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: nginx-ingress/create_ingress
# Strategy: native_shell
# Notes: create the Service and Ingress expected by the modern case, including
# the /app-style route rewrite that the imported vendored solver does not model.

static_solver_export_nginx_defaults

app_ns="${BENCH_NS_APP}"
ingress_ns="${BENCH_NS_INGRESS}"
service_name="${BENCH_PARAM_SERVICE_NAME:-demo-app}"
ingress_name="${BENCH_PARAM_INGRESS_NAME:-demo-route}"
host="${BENCH_PARAM_HOST:-demo.example.com}"
path="${BENCH_PARAM_PATH:-/app}"
service_port="${BENCH_PARAM_SERVICE_PORT:-80}"
target_port="${BENCH_PARAM_TARGET_PORT:-5678}"
ingress_class_name="${BENCH_PARAM_INGRESS_CLASS_NAME:-nginx}"
curl_pod_name="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
expected_body="${BENCH_PARAM_EXPECTED_BODY:-hello}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
manifest_path="${tmp_dir}/demo-expose.yaml"

cat > "${manifest_path}" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: ${service_name}
  namespace: ${app_ns}
spec:
  selector:
    app: demo-app
  ports:
    - name: http
      port: ${service_port}
      targetPort: ${target_port}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${ingress_name}
  namespace: ${app_ns}
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  ingressClassName: ${ingress_class_name}
  rules:
    - host: ${host}
      http:
        paths:
          - path: ${path}
            pathType: Prefix
            backend:
              service:
                name: ${service_name}
                port:
                  number: ${service_port}
EOF

kubectl apply -f "${manifest_path}" >/dev/null

wait_for_route() {
  local deadline body
  deadline=$((SECONDS + 120))
  while (( SECONDS < deadline )); do
    body="$(
      kubectl -n "${app_ns}" exec "${curl_pod_name}" -- \
        curl -sS -H "Host: ${host}" "http://ingress-nginx-controller.${ingress_ns}.svc${path}" 2>/dev/null || true
    )"
    body="${body//$'\r'/}"
    body="${body//$'\n'/}"
    if [[ "${body}" == "${expected_body}" ]]; then
      return 0
    fi
    sleep 3
  done
  static_solver_fail "ingress route did not become ready for ${host}${path}"
}

wait_for_route
static_solver_write_submit "created backend service and ingress route"
