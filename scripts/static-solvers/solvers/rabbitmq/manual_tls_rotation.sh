#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: rabbitmq/manual_tls_rotation
# Strategy: native_shell
# Notes: Rotate only the RabbitMQ leaf certificate, preserving the current CA.
# The imported reference expected an older secret name (${cluster}-tls-ca-key),
# while the current case preconditions persist the CA key as ${cluster}-tls-ca.

static_solver_export_namespace_if_unset "rabbitmq"

ns="${BENCH_NAMESPACE}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-rabbitmq}"
min_days="${BENCH_PARAM_MIN_ROTATED_LEAF_VALIDITY_DAYS:-300}"
validity=$((min_days + 30))
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

ca_secret="${BENCH_PARAM_TLS_CA_SECRET_NAME:-${cluster}-tls-ca}"
if ! kubectl -n "${ns}" get secret "${ca_secret}" >/dev/null 2>&1; then
  legacy_secret="${cluster}-tls-ca-key"
  if kubectl -n "${ns}" get secret "${legacy_secret}" >/dev/null 2>&1; then
    ca_secret="${legacy_secret}"
  else
    static_solver_fail "missing CA key secret: tried ${ca_secret} and ${legacy_secret}"
  fi
fi

kubectl -n "${ns}" get secret "${cluster}-tls" -o jsonpath='{.data.ca\.crt}' |
  base64 -d > "${tmp_dir}/ca.crt"
kubectl -n "${ns}" get secret "${ca_secret}" -o jsonpath='{.data.ca\.key}' |
  base64 -d > "${tmp_dir}/ca.key"

cat > "${tmp_dir}/openssl.cnf" <<EOF
distinguished_name=req_distinguished_name
req_extensions=v3_req
prompt=no
[req_distinguished_name]
CN=${cluster}
[v3_req]
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt_names
[alt_names]
DNS.1=${cluster}
DNS.2=${cluster}.${ns}
DNS.3=${cluster}.${ns}.svc
DNS.4=${cluster}.${ns}.svc.cluster.local
DNS.5=${cluster}-headless
DNS.6=${cluster}-headless.${ns}
DNS.7=${cluster}-headless.${ns}.svc
DNS.8=${cluster}-headless.${ns}.svc.cluster.local
DNS.9=${cluster}-0.${cluster}-headless.${ns}.svc.cluster.local
DNS.10=${cluster}-1.${cluster}-headless.${ns}.svc.cluster.local
DNS.11=${cluster}-2.${cluster}-headless.${ns}.svc.cluster.local
EOF

openssl genrsa -out "${tmp_dir}/tls.key" 2048
openssl req -new -key "${tmp_dir}/tls.key" -out "${tmp_dir}/tls.csr" -config "${tmp_dir}/openssl.cnf"
openssl x509 -req -in "${tmp_dir}/tls.csr" -CA "${tmp_dir}/ca.crt" -CAkey "${tmp_dir}/ca.key" \
  -CAcreateserial -out "${tmp_dir}/tls.crt" -days "${validity}" \
  -extensions v3_req -extfile "${tmp_dir}/openssl.cnf"

kubectl -n "${ns}" create secret generic "${cluster}-tls" \
  --from-file=ca.crt="${tmp_dir}/ca.crt" \
  --from-file=tls.crt="${tmp_dir}/tls.crt" \
  --from-file=tls.key="${tmp_dir}/tls.key" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -
kubectl -n "${ns}" rollout restart "statefulset/${cluster}"
kubectl -n "${ns}" rollout status "statefulset/${cluster}" --timeout=600s

static_solver_write_submit "rotated RabbitMQ leaf certificate"
