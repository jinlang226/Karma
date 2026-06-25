#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/secure-http-ingress
# Strategy: native_shell
# Notes: the archived solver assumes a standalone HTTP-only backend and patches
# the StatefulSet inline. The current branch typically inherits a secured
# Elasticsearch cluster from deploy-core-cluster, so the safe path is to leave
# the backend alone, create an ingress TLS secret, expose the existing backend
# through ingress-nginx, and wait for HTTPS ingress readiness explicitly.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
ingress_namespace="${BENCH_PARAM_INGRESS_NAMESPACE:-ingress-nginx}"
ingress_host="${BENCH_PARAM_INGRESS_HOST:-es.example.com}"
ingress_class="${BENCH_PARAM_INGRESS_CLASS_NAME:-nginx}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
elastic_secret="${BENCH_PARAM_CURRENT_PASSWORD_SECRET_NAME:-${BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME:-elastic-password}}"
elastic_secret_key="${BENCH_PARAM_CURRENT_PASSWORD_SECRET_KEY:-${BENCH_PARAM_ELASTIC_PASSWORD_KEY:-password}}"
ingress_service="${BENCH_PARAM_INGRESS_CONTROLLER_SERVICE_NAME:-ingress-nginx-controller}"
ingress_tls_secret="${BENCH_PARAM_INGRESS_TLS_SECRET_NAME:-es-secure-ingress-tls}"
ingress_name="${BENCH_PARAM_INGRESS_NAME:-es-secure-ingress}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

ELASTIC_PASSWORD=""
BACKEND_SCHEME=""

read_secret_value() {
  local secret_name="$1"
  local key_name="$2"
  kubectl -n "${ns}" get secret "${secret_name}" -o "jsonpath={.data.${key_name}}" 2>/dev/null | base64 -d 2>/dev/null || true
}

curl_http_code() {
  local -a args=("$@")
  kubectl -n "${ns}" exec "${curl_pod}" -- \
    curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 15 "${args[@]}"
}

curl_json() {
  local -a args=("$@")
  kubectl -n "${ns}" exec "${curl_pod}" -- \
    curl -sS --connect-timeout 5 --max-time 20 "${args[@]}"
}

detect_backend_scheme() {
  local output=""
  for scheme in https http; do
    output="$(curl_http_code -k "${scheme}://${service}.${ns}.svc:9200/" 2>/dev/null || true)"
    if [[ "${output}" =~ ^[0-9]{3}$ && "${output}" != "000" ]]; then
      printf '%s\n' "${scheme}"
      return 0
    fi
  done
  static_solver_fail "unable to detect live Elasticsearch backend scheme for ${service}.${ns}.svc"
}

generate_ingress_cert() {
  local conf="${tmp_dir}/openssl.cnf"
  cat > "${conf}" <<EOF
distinguished_name=dn
req_extensions=v3_req
prompt=no
[dn]
CN=${ingress_host}
[v3_req]
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=@alt_names
[alt_names]
DNS.1=${ingress_host}
EOF

  openssl genrsa -out "${tmp_dir}/ca.key" 2048 >/dev/null 2>&1
  openssl req -x509 -new -nodes -key "${tmp_dir}/ca.key" -sha256 -days 365 \
    -subj '/CN=es-secure-ingress-ca' -out "${tmp_dir}/ca.crt" >/dev/null 2>&1
  openssl genrsa -out "${tmp_dir}/tls.key" 2048 >/dev/null 2>&1
  openssl req -new -key "${tmp_dir}/tls.key" -out "${tmp_dir}/tls.csr" \
    -config "${conf}" >/dev/null 2>&1
  openssl x509 -req -in "${tmp_dir}/tls.csr" -CA "${tmp_dir}/ca.crt" \
    -CAkey "${tmp_dir}/ca.key" -CAcreateserial -out "${tmp_dir}/tls.crt" \
    -days 365 -extensions v3_req -extfile "${conf}" >/dev/null 2>&1
}

wait_for_ingress_ready() {
  local ingress_target="${ingress_service}.${ingress_namespace}.svc"
  local -a auth_args=()
  local https_output=""
  local http_code=""
  local deadline=$((SECONDS + 300))

  if [[ -n "${ELASTIC_PASSWORD}" ]]; then
    auth_args=(-u "elastic:${ELASTIC_PASSWORD}")
  fi

  while (( SECONDS < deadline )); do
    if ! curl_json "${auth_args[@]}" -k \
      "${BACKEND_SCHEME}://${service}.${ns}.svc:9200/_cluster/health?wait_for_status=yellow&timeout=5s" \
      >/dev/null 2>&1; then
      sleep 5
      continue
    fi

    https_output="$(
      curl_json "${auth_args[@]}" -k -H "Host: ${ingress_host}" \
        "https://${ingress_target}/_cluster/health" 2>/dev/null || true
    )"
    if [[ "${https_output}" != *'"status"'* ]]; then
      sleep 5
      continue
    fi

    http_code="$(
      curl_http_code -H "Host: ${ingress_host}" \
        "http://${ingress_target}/_cluster/health" 2>/dev/null || true
    )"
    if [[ "${http_code}" == "200" ]]; then
      sleep 5
      continue
    fi

    return 0
  done

  static_solver_fail "timed out waiting for HTTPS ingress readiness for ${ingress_host}"
}

generate_ingress_cert
kubectl -n "${ns}" create secret tls "${ingress_tls_secret}" \
  --cert="${tmp_dir}/tls.crt" \
  --key="${tmp_dir}/tls.key" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

BACKEND_SCHEME="$(detect_backend_scheme)"
BACKEND_PROTOCOL="$(printf '%s' "${BACKEND_SCHEME}" | tr '[:lower:]' '[:upper:]')"
ELASTIC_PASSWORD="$(read_secret_value "${elastic_secret}" "${elastic_secret_key}")"

kubectl -n "${ns}" apply -f - <<YAML
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${ingress_name}
  namespace: ${ns}
  annotations:
    nginx.ingress.kubernetes.io/backend-protocol: ${BACKEND_PROTOCOL}
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: ${ingress_class}
  tls:
    - hosts:
        - ${ingress_host}
      secretName: ${ingress_tls_secret}
  rules:
    - host: ${ingress_host}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: ${service}
                port:
                  number: 9200
YAML

wait_for_ingress_ready
static_solver_write_submit "enabled HTTPS ingress for Elasticsearch"
