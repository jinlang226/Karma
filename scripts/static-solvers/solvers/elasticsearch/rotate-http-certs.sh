#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/rotate-http-certs
# Strategy: direct_shell
# Notes: Rotate the HTTP CA/leaf material and recreate the curl helper pod
# without relying on envsubst being installed in the runtime.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
secret="${BENCH_PARAM_TLS_SECRET_NAME:-es-http-tls}"
ca_cm="${BENCH_PARAM_HTTP_CA_CONFIGMAP_NAME:-es-http-ca}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

kubectl -n "${ns}" exec openssl-toolbox -- /bin/sh -c "
set -e
rm -rf /tmp/rotated && mkdir -p /tmp/rotated
cat > /tmp/rotated/openssl.cnf <<EOF
distinguished_name=dn
req_extensions=v3_req
prompt=no
[dn]
CN=${service}
[v3_req]
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt_names
[alt_names]
DNS.1=localhost
DNS.2=${service}
DNS.3=${prefix}
DNS.4=*.svc
DNS.5=*.svc.cluster.local
EOF
openssl genrsa -out /tmp/rotated/ca.key 2048
openssl req -x509 -new -nodes -key /tmp/rotated/ca.key -sha256 -days 365 \
  -subj '/CN=es-http-rotated-ca' -out /tmp/rotated/ca.crt
openssl genrsa -out /tmp/rotated/tls.key 2048
openssl req -new -key /tmp/rotated/tls.key -out /tmp/rotated/tls.csr \
  -config /tmp/rotated/openssl.cnf
openssl x509 -req -in /tmp/rotated/tls.csr -CA /tmp/rotated/ca.crt \
  -CAkey /tmp/rotated/ca.key -CAcreateserial -out /tmp/rotated/tls.crt \
  -days 365 -extensions v3_req -extfile /tmp/rotated/openssl.cnf
"

kubectl -n "${ns}" cp openssl-toolbox:/tmp/rotated "${tmp_dir}/rotated"

kubectl -n "${ns}" create secret generic "${secret}" \
  --from-file=tls.crt="${tmp_dir}/rotated/tls.crt" \
  --from-file=tls.key="${tmp_dir}/rotated/tls.key" \
  --from-file=ca.crt="${tmp_dir}/rotated/ca.crt" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

kubectl -n "${ns}" create configmap "${ca_cm}" \
  --from-file=ca.crt="${tmp_dir}/rotated/ca.crt" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

kubectl -n "${ns}" rollout restart "statefulset/${prefix}"
kubectl -n "${ns}" rollout status "statefulset/${prefix}" --timeout=900s

kubectl -n "${ns}" delete pod "${curl_pod}" --ignore-not-found=true --wait=false || true

for _ in $(seq 1 30); do
  if ! kubectl -n "${ns}" get "pod/${curl_pod}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if kubectl -n "${ns}" get "pod/${curl_pod}" >/dev/null 2>&1; then
  kubectl -n "${ns}" delete pod "${curl_pod}" --ignore-not-found=true --force --grace-period=0 --wait=false || true
fi

for _ in $(seq 1 30); do
  if ! kubectl -n "${ns}" get "pod/${curl_pod}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if kubectl -n "${ns}" get "pod/${curl_pod}" >/dev/null 2>&1; then
  static_solver_fail "curl helper pod ${curl_pod} is still present after forced deletion"
fi

curl_manifest="${STATIC_SOLVER_STAGE_DIR}/curl-test.yaml"
cat > "${curl_manifest}" <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${curl_pod}
  namespace: ${ns}
spec:
  containers:
    - name: curl
      image: curlimages/curl:8.6.0
      command: ["sleep", "infinity"]
      volumeMounts:
        - name: es-http-ca
          mountPath: /etc/es-http-ca
          readOnly: true
  volumes:
    - name: es-http-ca
      configMap:
        name: ${ca_cm}
        items:
          - key: ca.crt
            path: ca.crt
EOF

kubectl -n "${ns}" apply -f "${curl_manifest}"
kubectl -n "${ns}" wait --for=condition=ready "pod/${curl_pod}" --timeout=300s

static_solver_write_submit "rotated HTTP CA and leaf certificate"
