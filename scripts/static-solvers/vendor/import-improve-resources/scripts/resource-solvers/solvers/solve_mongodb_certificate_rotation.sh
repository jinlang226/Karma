#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongo-rs}"
service="${BENCH_PARAM_SERVICE_NAME:-mongo}"
ca_secret="${BENCH_PARAM_TLS_CA_SECRET_NAME:-mongodb-tls-ca}"
cert_secret="${BENCH_PARAM_TLS_CERT_SECRET_NAME:-mongodb-tls-cert}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
kubectl -n "$ns" get secret "$ca_secret" -o jsonpath='{.data.ca\.crt}' | base64 -d > "$tmp/ca.crt"
kubectl -n "$ns" get secret "$ca_secret" -o jsonpath='{.data.ca\.key}' | base64 -d > "$tmp/ca.key"
cat > "$tmp/openssl.cnf" <<EOF
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
openssl genrsa -out "$tmp/server.key" 2048
openssl req -new -key "$tmp/server.key" -out "$tmp/server.csr" -config "$tmp/openssl.cnf"
openssl x509 -req -in "$tmp/server.csr" -CA "$tmp/ca.crt" -CAkey "$tmp/ca.key" \
  -CAcreateserial -out "$tmp/server.crt" -days 365 -extensions v3_req -extfile "$tmp/openssl.cnf"
cat "$tmp/server.crt" "$tmp/server.key" > "$tmp/server.pem"
kubectl -n "$ns" create secret generic "$cert_secret" --from-file=server.pem="$tmp/server.pem" \
  --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
kubectl -n "$ns" rollout restart "statefulset/${cluster}"
kubectl -n "$ns" rollout status "statefulset/${cluster}" --timeout=600s
printf 'rotated MongoDB server certificate\n' > submit.txt
