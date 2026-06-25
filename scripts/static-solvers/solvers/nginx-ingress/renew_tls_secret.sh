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
ca_crt="/tmp/test-ca.crt"
ca_key="/tmp/test-ca.key"
min_seconds="${BENCH_PARAM_MIN_VALIDITY_SECONDS:-86400}"
days=$(( (min_seconds + 86399) / 86400 + 1 ))

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

[[ -f "${ca_crt}" ]] || static_solver_fail "missing CA cert at ${ca_crt}"
[[ -f "${ca_key}" ]] || static_solver_fail "missing CA key at ${ca_key}"

ingress_name="${default_ingress}"
if kubectl -n "${app_ns}" get ingress "${ingress_name}" >/dev/null 2>&1; then
  :
else
  ingress_name="$(kubectl -n "${app_ns}" get ingress -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
fi
[[ -n "${ingress_name}" ]] || static_solver_fail "could not determine ingress name in namespace ${app_ns}"

host="$(kubectl -n "${app_ns}" get ingress "${ingress_name}" -o jsonpath='{.spec.rules[0].host}' 2>/dev/null || true)"
[[ -n "${host}" ]] || host="${BENCH_PARAM_HOST:-demo.example.com}"

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

kubectl -n "${ingress_ns}" rollout restart deploy/ingress-nginx-controller
kubectl -n "${ingress_ns}" rollout status deploy/ingress-nginx-controller --timeout=180s

static_solver_write_submit "renewed ingress TLS secret with CA-signed leaf certificate"
