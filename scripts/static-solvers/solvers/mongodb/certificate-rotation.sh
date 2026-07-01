#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/certificate-rotation
# Strategy: native_shell
# Notes: Rotates only the server certificate while preserving the CA and any
# existing client.pem so later TLS-aware stages can keep using the same bundle.

static_solver_export_namespace_if_unset "mongodb"

ns="${BENCH_NAMESPACE}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongo-rs}"
service="${BENCH_PARAM_SERVICE_NAME:-mongo}"
openssl_pod="${BENCH_PARAM_OPENSSL_POD_NAME:-openssl-toolbox}"
ca_secret="${BENCH_PARAM_TLS_CA_SECRET_NAME:-mongodb-tls-ca}"
cert_secret="${BENCH_PARAM_TLS_CERT_SECRET_NAME:-mongodb-tls-cert}"
uri="mongodb://localhost:27017/?directConnection=true"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

kubectl -n "${ns}" get statefulset "${cluster}" >/dev/null
kubectl -n "${ns}" get secret "${ca_secret}" -o jsonpath='{.data.ca\.crt}' | base64 -d > "${tmp}/ca.crt"
kubectl -n "${ns}" get secret "${ca_secret}" -o jsonpath='{.data.ca\.key}' | base64 -d > "${tmp}/ca.key"
kubectl -n "${ns}" get secret "${cert_secret}" -o jsonpath='{.data.client\.pem}' 2>/dev/null | \
  base64 -d > "${tmp}/client.pem" || true

if ! kubectl -n "${ns}" get pod "${openssl_pod}" >/dev/null 2>&1; then
  kubectl -n "${ns}" apply -f \
    "${STATIC_SOLVER_REPO_ROOT}/cases/mongodb/certificate-rotation/resource/openssl-toolbox.yaml"
fi
kubectl -n "${ns}" wait --for=condition=ready "pod/${openssl_pod}" --timeout=300s

cat > "${tmp}/openssl.cnf" <<EOF
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
DNS.1=localhost
DNS.2=${service}
DNS.3=${service}.${ns}
DNS.4=${service}.${ns}.svc
DNS.5=${service}.${ns}.svc.cluster.local
DNS.6=${cluster}-0.${service}.${ns}.svc.cluster.local
DNS.7=${cluster}-1.${service}.${ns}.svc.cluster.local
DNS.8=${cluster}-2.${service}.${ns}.svc.cluster.local
EOF
openssl genrsa -out "${tmp}/server.key" 2048
openssl req -new -key "${tmp}/server.key" -out "${tmp}/server.csr" -config "${tmp}/openssl.cnf"
openssl x509 -req -in "${tmp}/server.csr" -CA "${tmp}/ca.crt" -CAkey "${tmp}/ca.key" \
  -CAcreateserial -out "${tmp}/server.crt" -days 365 -extensions v3_req -extfile "${tmp}/openssl.cnf"
cat "${tmp}/server.crt" "${tmp}/server.key" > "${tmp}/server.pem"

create_secret_args=(
  create secret generic "${cert_secret}"
  --from-file=server.pem="${tmp}/server.pem"
)
if [[ -s "${tmp}/client.pem" ]]; then
  create_secret_args+=(--from-file=client.pem="${tmp}/client.pem")
fi
kubectl -n "${ns}" "${create_secret_args[@]}" --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

kubectl -n "${ns}" rollout restart "statefulset/${cluster}"
kubectl -n "${ns}" rollout status "statefulset/${cluster}" --timeout=600s

for _ in $(seq 1 60); do
  if kubectl -n "${ns}" exec "${cluster}-0" -- \
    mongosh --quiet "${uri}" \
    --tls \
    --tlsAllowInvalidHostnames \
    --tlsCAFile /etc/mongo-ca/ca.crt \
    --eval 'db.hello().ok' | grep -qx 1; then
    static_solver_write_submit "rotated MongoDB server certificate"
    exit 0
  fi
  sleep 3
done

static_solver_fail "MongoDB TLS service did not recover after certificate rotation"
