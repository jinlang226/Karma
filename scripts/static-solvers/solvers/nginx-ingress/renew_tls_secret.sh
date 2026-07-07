#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: nginx-ingress/renew_tls_secret
# Strategy: native_shell
# Notes: Renew the existing TLS secret with a fresh leaf certificate signed by
# the precondition-provided test CA, so the oracle's --cacert validation passes.

static_solver_export_nginx_defaults

app_ns="${BENCH_NS_APP}"
ingress_ns="${BENCH_NS_INGRESS}"
default_ingress="${BENCH_PARAM_INGRESS_NAME:-demo-ingress}"
min_seconds="${BENCH_PARAM_MIN_VALIDITY_SECONDS:-86400}"
days=$(( (min_seconds + 86399) / 86400 + 1 ))
desired_host="${BENCH_PARAM_HOST:-demo.example.com}"
expected_body="${BENCH_PARAM_EXPECTED_BODY:-hello}"
curl_pod_name="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
cluster_key="$(basename "${KUBECONFIG:-default}")"
test_ca_configmap="${BENCH_PARAM_TEST_CA_CONFIGMAP_NAME:-test-ca}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
ingress_json="${tmp_dir}/ingress.json"
patched_ingress_json="${tmp_dir}/ingress-patched.json"
curl_test_manifest="${STATIC_SOLVER_REPO_ROOT}/cases/nginx-ingress/renew_tls_secret/resource/curl-test.yaml"

resolve_first_existing_file() {
  local path=""
  for path in "$@"; do
    if [[ -f "${path}" ]]; then
      printf '%s\n' "${path}"
      return 0
    fi
  done
  return 1
}

ensure_test_ca_materials() {
  local generated_key="${tmp_dir}/test-ca.${cluster_key}.key"
  local generated_csr="${tmp_dir}/test-ca.${cluster_key}.csr"
  local generated_crt="${tmp_dir}/test-ca.${cluster_key}.crt"

  openssl genrsa -out "${generated_key}" 2048 >/dev/null 2>&1
  openssl req -new -key "${generated_key}" -subj "/CN=Test CA" -out "${generated_csr}" >/dev/null 2>&1
  openssl x509 -req \
    -in "${generated_csr}" \
    -signkey "${generated_key}" \
    -set_serial 01 \
    -extfile "${STATIC_SOLVER_REPO_ROOT}/cases/nginx-ingress/renew_tls_secret/resource/ca-ext.conf" \
    -days 9000 \
    -out "${generated_crt}" >/dev/null 2>&1

  install -m 600 "${generated_key}" "/tmp/test-ca.${cluster_key}.key"
  install -m 644 "${generated_crt}" "/tmp/test-ca.${cluster_key}.crt"

  kubectl -n "${app_ns}" create configmap "${test_ca_configmap}" \
    --from-file=ca.crt="/tmp/test-ca.${cluster_key}.crt" \
    --dry-run=client -o yaml | kubectl -n "${app_ns}" apply -f -
}

refresh_test_ca_bundle() {
  kubectl -n "${app_ns}" create configmap "${test_ca_configmap}" \
    --from-file=ca.crt="${ca_crt}" \
    --dry-run=client -o yaml | kubectl -n "${app_ns}" apply -f -

  kubectl -n "${app_ns}" delete pod "${curl_pod_name}" --ignore-not-found=true --wait=true >/dev/null
  if ! kubectl -n "${app_ns}" get pod "${curl_pod_name}" >/dev/null 2>&1; then
    kubectl -n "${app_ns}" apply -f "${curl_test_manifest}" >/dev/null
  fi
  kubectl -n "${app_ns}" wait --for=condition=Ready "pod/${curl_pod_name}" --timeout=180s >/dev/null
}

load_ingress_endpoint() {
  local env_file="" line=""

  ingress_node_ip="$(
    kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true
  )"
  ingress_https_port="$(
    kubectl get svc ingress-nginx-controller -n "${ingress_ns}" -o jsonpath='{.spec.ports[?(@.port==443)].nodePort}' 2>/dev/null || true
  )"
  if [[ -n "${ingress_node_ip}" && -n "${ingress_https_port}" ]]; then
    return 0
  fi

  for env_file in "/tmp/ingress_env.${cluster_key}" "/tmp/ingress_env"; do
    [[ -f "${env_file}" ]] || continue
    while IFS= read -r line; do
      line="${line#export }"
      case "${line}" in
        INGRESS_NODE_IP=*)
          [[ -n "${ingress_node_ip:-}" ]] || ingress_node_ip="${line#INGRESS_NODE_IP=}"
          ;;
        INGRESS_HTTPS_PORT=*)
          [[ -n "${ingress_https_port:-}" ]] || ingress_https_port="${line#INGRESS_HTTPS_PORT=}"
          ;;
      esac
    done < "${env_file}"
    [[ -n "${ingress_node_ip:-}" && -n "${ingress_https_port:-}" ]] && return 0
  done

  return 1
}

wait_for_tls_route() {
  local deadline body last_error err_file
  err_file="${tmp_dir}/wait-for-tls.err"
  deadline=$((SECONDS + 180))
  while (( SECONDS < deadline )); do
    if body="$(
      kubectl -n "${app_ns}" exec "${curl_pod_name}" -- \
        curl -sS \
          --connect-timeout 5 \
          --max-time 15 \
          --cacert /tmp/tls/ca.crt \
          --resolve "${host}:${ingress_https_port}:${ingress_node_ip}" \
          "https://${host}:${ingress_https_port}/" 2>"${err_file}"
    )"; then
      body="${body//$'\r'/}"
      body="${body//$'\n'/}"
      if [[ "${body}" == "${expected_body}" ]]; then
        return 0
      fi
      last_error="unexpected body: ${body}"
    else
      last_error="$(tr '\r\n' '  ' < "${err_file}" | sed 's/  */ /g')"
    fi
    sleep 3
  done
  static_solver_fail "renewed TLS route did not serve expected body for host ${host}: ${last_error:-no response}"
}

ca_crt="$(resolve_first_existing_file "/tmp/test-ca.${cluster_key}.crt" "/tmp/test-ca.crt" || true)"
ca_key="$(resolve_first_existing_file "/tmp/test-ca.${cluster_key}.key" "/tmp/test-ca.key" || true)"

if [[ -z "${ca_crt}" || -z "${ca_key}" ]]; then
  ensure_test_ca_materials
  ca_crt="/tmp/test-ca.${cluster_key}.crt"
  ca_key="/tmp/test-ca.${cluster_key}.key"
fi

[[ -n "${ca_crt}" ]] || static_solver_fail "missing CA cert at /tmp/test-ca.${cluster_key}.crt or /tmp/test-ca.crt"
[[ -n "${ca_key}" ]] || static_solver_fail "missing CA key at /tmp/test-ca.${cluster_key}.key or /tmp/test-ca.key"

refresh_test_ca_bundle

ingress_name="${default_ingress}"
if kubectl -n "${app_ns}" get ingress "${ingress_name}" >/dev/null 2>&1; then
  :
else
  ingress_name="$(kubectl -n "${app_ns}" get ingress -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
fi
[[ -n "${ingress_name}" ]] || static_solver_fail "could not determine ingress name in namespace ${app_ns}"

kubectl -n "${app_ns}" get ingress "${ingress_name}" -o json > "${ingress_json}"
host="${desired_host}"

secret_name="$(kubectl -n "${app_ns}" get ingress "${ingress_name}" -o jsonpath='{.spec.tls[0].secretName}' 2>/dev/null || true)"
[[ -n "${secret_name}" ]] || secret_name="${BENCH_PARAM_TLS_SECRET_NAME:-expired-tls-secret}"

leaf_cnf="${tmp_dir}/leaf.cnf"
cat > "${leaf_cnf}" <<EOF
[ req ]
default_bits       = 2048
prompt             = no
default_md         = sha256
distinguished_name = dn
req_extensions     = req_ext

[ dn ]
CN = ${host}

[ req_ext ]
subjectAltName = @alt_names

[ alt_names ]
DNS.1 = ${host}
EOF

openssl genrsa -out "${tmp_dir}/tls.key" 2048 >/dev/null 2>&1
openssl req -new -key "${tmp_dir}/tls.key" -out "${tmp_dir}/tls.csr" -config "${leaf_cnf}" >/dev/null 2>&1
openssl x509 -req \
  -in "${tmp_dir}/tls.csr" \
  -CA "${ca_crt}" \
  -CAkey "${ca_key}" \
  -CAcreateserial \
  -out "${tmp_dir}/tls.crt" \
  -days "${days}" \
  -sha256 \
  -extfile "${leaf_cnf}" \
  -extensions req_ext >/dev/null 2>&1

kubectl -n "${app_ns}" create secret tls "${secret_name}" \
  --cert="${tmp_dir}/tls.crt" \
  --key="${tmp_dir}/tls.key" \
  --dry-run=client -o yaml | kubectl -n "${app_ns}" apply -f -

python3 - "${ingress_json}" "${patched_ingress_json}" "${host}" "${secret_name}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
host = sys.argv[3]
secret_name = sys.argv[4]

payload = json.loads(source_path.read_text())
payload.pop("status", None)
metadata = payload.get("metadata", {})
for key in ("creationTimestamp", "generation", "managedFields", "resourceVersion", "uid"):
    metadata.pop(key, None)

spec = payload.get("spec") or {}
rules = spec.get("rules") or []
if rules:
    rules[0]["host"] = host

tls_entries = spec.get("tls") or []
if tls_entries:
    tls_entries[0]["secretName"] = secret_name
    tls_entries[0]["hosts"] = [host]
else:
    spec["tls"] = [{"hosts": [host], "secretName": secret_name}]

output_path.write_text(json.dumps(payload))
PY

kubectl -n "${app_ns}" apply -f "${patched_ingress_json}"

kubectl -n "${ingress_ns}" rollout restart deploy/ingress-nginx-controller
kubectl -n "${ingress_ns}" rollout status deploy/ingress-nginx-controller --timeout=180s

load_ingress_endpoint || static_solver_fail "could not determine ingress HTTPS endpoint"
wait_for_tls_route

static_solver_write_submit "renewed ingress TLS secret with CA-signed leaf certificate"
