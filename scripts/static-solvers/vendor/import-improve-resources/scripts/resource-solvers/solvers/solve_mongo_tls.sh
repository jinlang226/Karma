#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongo-rs}"
service="${BENCH_PARAM_SERVICE_NAME:-mongo}"
ca_secret="${BENCH_PARAM_TLS_CA_SECRET_NAME:-mongodb-tls-ca}"
cert_secret="${BENCH_PARAM_TLS_CERT_SECRET_NAME:-mongodb-tls-cert}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

kubectl -n "$ns" exec openssl-toolbox -- sh -c "
set -e
rm -rf /tmp/mongo-tls && mkdir -p /tmp/mongo-tls
cd /tmp/mongo-tls
openssl genrsa -out ca.key 2048
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 -subj '/CN=MongoDB CA' -out ca.crt
cat > server.cnf <<EOF
[req]
distinguished_name=dn
req_extensions=req_ext
prompt=no
[dn]
CN=${service}
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
DNS.6=localhost
IP.1=127.0.0.1
EOF
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -config server.cnf
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 365 -extensions req_ext -extfile server.cnf
cat server.key server.crt > mongodb.pem
"
kubectl -n "$ns" cp openssl-toolbox:/tmp/mongo-tls "$tmp/certs"
kubectl -n "$ns" create secret generic "$ca_secret" \
  --from-file=ca.crt="$tmp/certs/ca.crt" --dry-run=client -o yaml |
  kubectl -n "$ns" apply -f -
kubectl -n "$ns" create secret generic "$cert_secret" \
  --from-file=mongodb.pem="$tmp/certs/mongodb.pem" --dry-run=client -o yaml |
  kubectl -n "$ns" apply -f -
python3 - <<'PY' | kubectl -n "$BENCH_NAMESPACE" apply -f -
import json
import os
import subprocess
ns=os.environ["BENCH_NAMESPACE"]
cluster=os.environ.get("BENCH_PARAM_CLUSTER_PREFIX","mongo-rs")
ca=os.environ.get("BENCH_PARAM_TLS_CA_SECRET_NAME","mongodb-tls-ca")
cert=os.environ.get("BENCH_PARAM_TLS_CERT_SECRET_NAME","mongodb-tls-cert")
obj=json.loads(subprocess.check_output(
    ["kubectl","-n",ns,"get","sts",cluster,"-o","json"], text=True
))
obj.pop("status",None)
spec=obj["spec"]["template"]["spec"]
container=spec["containers"][0]
command=[x for x in container.get("command",[]) if not str(x).startswith("--tls")]
command += [
    "--tlsMode","requireTLS",
    "--tlsCertificateKeyFile","/etc/mongo-tls/mongodb.pem",
    "--tlsCAFile","/etc/mongo-ca/ca.crt",
    "--tlsClusterFile","/etc/mongo-tls/mongodb.pem",
    "--tlsClusterCAFile","/etc/mongo-ca/ca.crt",
    "--tlsAllowConnectionsWithoutCertificates",
]
container["command"]=command
mounts=[m for m in container.get("volumeMounts",[]) if m.get("name") not in {"mongo-ca","mongo-tls"}]
mounts += [
    {"name":"mongo-ca","mountPath":"/etc/mongo-ca","readOnly":True},
    {"name":"mongo-tls","mountPath":"/etc/mongo-tls","readOnly":True},
]
container["volumeMounts"]=mounts
volumes=[v for v in spec.get("volumes",[]) if v.get("name") not in {"mongo-ca","mongo-tls"}]
volumes += [
    {"name":"mongo-ca","secret":{"secretName":ca}},
    {"name":"mongo-tls","secret":{"secretName":cert,"defaultMode":256}},
]
spec["volumes"]=volumes
print(json.dumps(obj))
PY
kubectl -n "$ns" rollout status "statefulset/${cluster}" --timeout=900s
printf 'enabled MongoDB TLS\n' > submit.txt
