#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
secret="${BENCH_PARAM_CERT_SECRET_NAME:-crdb-cluster-certs}"
validity="${BENCH_PARAM_CERT_VALIDITY_DAYS:-365}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/node.cnf" <<EOF
[req]
distinguished_name=req_distinguished_name
req_extensions=v3_req
prompt=no
[req_distinguished_name]
CN=node
[v3_req]
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt_names
[alt_names]
DNS.1=localhost
IP.1=127.0.0.1
DNS.2=${cluster}
DNS.3=${cluster}.${ns}
DNS.4=${cluster}.${ns}.svc
DNS.5=${cluster}.${ns}.svc.cluster.local
DNS.6=*.${cluster}.${ns}.svc.cluster.local
EOF
openssl genrsa -out "$tmp/ca.key" 2048
openssl req -x509 -new -nodes -key "$tmp/ca.key" -sha256 -days 3650 \
  -subj "/CN=CockroachDB CA" -out "$tmp/ca.crt"
openssl genrsa -out "$tmp/node.key" 2048
openssl req -new -key "$tmp/node.key" -out "$tmp/node.csr" -config "$tmp/node.cnf"
openssl x509 -req -in "$tmp/node.csr" -CA "$tmp/ca.crt" -CAkey "$tmp/ca.key" \
  -CAcreateserial -out "$tmp/node.crt" -days "$validity" \
  -extensions v3_req -extfile "$tmp/node.cnf"
openssl genrsa -out "$tmp/client.root.key" 2048
openssl req -new -key "$tmp/client.root.key" -subj "/CN=root" -out "$tmp/client.root.csr"
openssl x509 -req -in "$tmp/client.root.csr" -CA "$tmp/ca.crt" -CAkey "$tmp/ca.key" \
  -CAcreateserial -out "$tmp/client.root.crt" -days "$validity"
kubectl -n "$ns" create secret generic "$secret" \
  --from-file=ca.crt="$tmp/ca.crt" \
  --from-file=node.crt="$tmp/node.crt" \
  --from-file=node.key="$tmp/node.key" \
  --from-file=client.root.crt="$tmp/client.root.crt" \
  --from-file=client.root.key="$tmp/client.root.key" \
  --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
cat > "$tmp/patch.json" <<EOF
{
  "spec": {
    "template": {
      "spec": {
        "containers": [{
          "name": "db",
          "command": ["/bin/bash", "-c", "exec /cockroach/cockroach start --logtostderr=INFO --certs-dir=/cockroach/cockroach-certs --advertise-host=\$(POD_NAME).${cluster}.\$(POD_NAMESPACE).svc.cluster.local --http-addr=0.0.0.0:8080 --port=26257 --cache=25% --max-sql-memory=25% --join=${cluster}-0.${cluster}.\$(POD_NAMESPACE).svc.cluster.local:26257,${cluster}-1.${cluster}.\$(POD_NAMESPACE).svc.cluster.local:26257,${cluster}-2.${cluster}.\$(POD_NAMESPACE).svc.cluster.local:26257"],
          "volumeMounts": [{
            "name": "cockroach-certs",
            "mountPath": "/cockroach/cockroach-certs",
            "readOnly": true
          }],
          "livenessProbe": {"httpGet": {"scheme": "HTTPS"}},
          "readinessProbe": {"httpGet": {"scheme": "HTTPS"}}
        }],
        "volumes": [{
          "name": "cockroach-certs",
          "secret": {"secretName": "${secret}", "defaultMode": 256}
        }]
      }
    }
  }
}
EOF
kubectl -n "$ns" patch "statefulset/${cluster}" --type=strategic --patch-file "$tmp/patch.json"
kubectl -n "$ns" delete pod -l "app.kubernetes.io/instance=${cluster}" \
  --wait=true --timeout=180s
kubectl -n "$ns" wait --for=condition=ready pod \
  -l "app.kubernetes.io/instance=${cluster}" --timeout=600s
kubectl -n "$ns" exec "${cluster}-0" -- ./cockroach sql \
  --certs-dir=/cockroach/cockroach-certs -e 'SELECT 1;' >/dev/null
printf 'enabled CockroachDB TLS\n' > submit.txt
