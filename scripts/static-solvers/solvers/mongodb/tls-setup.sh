#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/tls-setup
# Strategy: native_shell
# Notes: Enables the case's /etc/tls client bundle while also preserving the
# /etc/mongo-ca and /etc/mongo-cert layout used by certificate-rotation.

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

if ! kubectl -n "${ns}" get pod "${openssl_pod}" >/dev/null 2>&1; then
  kubectl -n "${ns}" apply -f \
    "${STATIC_SOLVER_REPO_ROOT}/cases/mongodb/tls-setup/resource/openssl-toolbox.yaml"
fi
kubectl -n "${ns}" wait --for=condition=ready "pod/${openssl_pod}" --timeout=300s

static_solver_log "generating TLS materials for MongoDB cluster ${cluster}"
kubectl -n "${ns}" exec "${openssl_pod}" -- sh -c "
set -e
rm -rf /tmp/mongo-tls
mkdir -p /tmp/mongo-tls
cd /tmp/mongo-tls
cat > server.cnf <<'EOF'
[req]
distinguished_name=dn
req_extensions=req_ext
prompt=no
[dn]
CN=${cluster}
[req_ext]
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt
[alt]
DNS.1=${service}
DNS.2=${service}.${ns}
DNS.3=${service}.${ns}.svc
DNS.4=${service}.${ns}.svc.cluster.local
DNS.5=*.${service}.${ns}.svc.cluster.local
DNS.6=${cluster}-0.${service}.${ns}.svc.cluster.local
DNS.7=${cluster}-1.${service}.${ns}.svc.cluster.local
DNS.8=${cluster}-2.${service}.${ns}.svc.cluster.local
DNS.9=localhost
IP.1=127.0.0.1
EOF
cat > client.cnf <<'EOF'
[req]
distinguished_name=dn
req_extensions=req_ext
prompt=no
[dn]
CN=mongo-client
[req_ext]
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=clientAuth
EOF
openssl genrsa -out ca.key 2048
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -subj '/CN=MongoDB CA' -out ca.crt
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -config server.cnf
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 365 -extensions req_ext -extfile server.cnf
cat server.crt server.key > server.pem
openssl genrsa -out client.key 2048
openssl req -new -key client.key -out client.csr -config client.cnf
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out client.crt -days 365 -extensions req_ext -extfile client.cnf
cat client.crt client.key > client.pem
"

for file in ca.crt ca.key server.pem client.pem; do
  kubectl -n "${ns}" exec "${openssl_pod}" -- cat "/tmp/mongo-tls/${file}" > "${tmp}/${file}"
done

kubectl -n "${ns}" create secret generic "${ca_secret}" \
  --from-file=ca.crt="${tmp}/ca.crt" \
  --from-file=ca.key="${tmp}/ca.key" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -
kubectl -n "${ns}" create secret generic "${cert_secret}" \
  --from-file=server.pem="${tmp}/server.pem" \
  --from-file=client.pem="${tmp}/client.pem" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

python3 - <<'PY' | kubectl -n "${BENCH_NAMESPACE}" apply -f -
import json
import os
import subprocess

ns = os.environ["BENCH_NAMESPACE"]
cluster = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongo-rs")
ca_secret = os.environ.get("BENCH_PARAM_TLS_CA_SECRET_NAME", "mongodb-tls-ca")
cert_secret = os.environ.get("BENCH_PARAM_TLS_CERT_SECRET_NAME", "mongodb-tls-cert")
obj = json.loads(
    subprocess.check_output(
        ["kubectl", "-n", ns, "get", "sts", cluster, "-o", "json"],
        text=True,
    )
)
obj.pop("status", None)
metadata = obj.get("metadata", {})
for key in ("creationTimestamp", "generation", "managedFields", "resourceVersion", "uid"):
    metadata.pop(key, None)
spec = obj["spec"]["template"]["spec"]
container = spec["containers"][0]
value_flags = {
    "--tlsMode",
    "--tlsCertificateKeyFile",
    "--tlsCAFile",
    "--tlsClusterFile",
    "--tlsClusterCAFile",
}
filtered_command = []
skip_next = False
for raw_arg in container.get("command", []):
    arg = str(raw_arg)
    if skip_next:
        skip_next = False
        continue
    if arg in value_flags:
        skip_next = True
        continue
    if any(arg.startswith(flag + "=") for flag in value_flags):
        continue
    if arg.startswith("--tls"):
        continue
    filtered_command.append(raw_arg)
filtered_command += [
    "--tlsMode",
    "requireTLS",
    "--tlsCertificateKeyFile",
    "/etc/mongo-cert/server.pem",
    "--tlsCAFile",
    "/etc/mongo-ca/ca.crt",
    "--tlsAllowConnectionsWithoutCertificates",
]
container["command"] = filtered_command

mount_names = {"mongo-ca", "mongo-cert", "mongo-tls", "mongo-tls-bundle"}
mount_paths = {"/etc/mongo-ca", "/etc/mongo-cert", "/etc/mongo-tls", "/etc/tls"}
mounts = [
    mount
    for mount in container.get("volumeMounts", [])
    if mount.get("name") not in mount_names and mount.get("mountPath") not in mount_paths
]
mounts += [
    {"name": "mongo-ca", "mountPath": "/etc/mongo-ca", "readOnly": True},
    {"name": "mongo-cert", "mountPath": "/etc/mongo-cert", "readOnly": True},
    {"name": "mongo-tls-bundle", "mountPath": "/etc/tls", "readOnly": True},
]
container["volumeMounts"] = mounts

volumes = [
    volume
    for volume in spec.get("volumes", [])
    if volume.get("name") not in mount_names
]
volumes += [
    {"name": "mongo-ca", "secret": {"secretName": ca_secret}},
    {
        "name": "mongo-cert",
        "secret": {"secretName": cert_secret, "defaultMode": 256},
    },
    {
        "name": "mongo-tls-bundle",
        "projected": {
            "sources": [
                {"secret": {"name": ca_secret, "items": [{"key": "ca.crt", "path": "ca.crt"}]}},
                {
                    "secret": {
                        "name": cert_secret,
                        "items": [
                            {"key": "server.pem", "path": "server.pem"},
                            {"key": "client.pem", "path": "client.pem"},
                        ],
                    }
                },
            ]
        },
    },
]
spec["volumes"] = volumes
print(json.dumps(obj))
PY

kubectl -n "${ns}" rollout status "statefulset/${cluster}" --timeout=900s

for _ in $(seq 1 60); do
  if kubectl -n "${ns}" exec "${cluster}-0" -- \
    mongosh --quiet "${uri}" \
    --tls \
    --tlsAllowInvalidHostnames \
    --tlsCAFile /etc/tls/ca.crt \
    --tlsCertificateKeyFile /etc/tls/client.pem \
    --eval 'db.adminCommand({ping:1}).ok' | grep -qx 1; then
    static_solver_write_submit "enabled MongoDB TLS"
    exit 0
  fi
  sleep 3
done

static_solver_fail "MongoDB TLS endpoint did not become ready"
