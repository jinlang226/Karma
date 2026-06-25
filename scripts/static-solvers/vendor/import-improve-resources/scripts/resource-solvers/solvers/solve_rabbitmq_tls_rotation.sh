#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-rabbitmq}"
min_days="${BENCH_PARAM_MIN_ROTATED_LEAF_VALIDITY_DAYS:-300}"
validity=$((min_days + 30))
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

kubectl -n "$ns" get secret "${cluster}-tls" -o jsonpath='{.data.ca\.crt}' |
  base64 -d > "$tmp/ca.crt"
kubectl -n "$ns" get secret "${cluster}-tls-ca-key" -o jsonpath='{.data.ca\.key}' |
  base64 -d > "$tmp/ca.key"

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

openssl genrsa -out "$tmp/tls.key" 2048
openssl req -new -key "$tmp/tls.key" -out "$tmp/tls.csr" -config "$tmp/openssl.cnf"
openssl x509 -req -in "$tmp/tls.csr" -CA "$tmp/ca.crt" -CAkey "$tmp/ca.key" \
  -CAcreateserial -out "$tmp/tls.crt" -days "$validity" \
  -extensions v3_req -extfile "$tmp/openssl.cnf"

kubectl -n "$ns" create secret generic "${cluster}-tls" \
  --from-file=ca.crt="$tmp/ca.crt" \
  --from-file=tls.crt="$tmp/tls.crt" \
  --from-file=tls.key="$tmp/tls.key" \
  --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
kubectl -n "$ns" rollout restart "statefulset/${cluster}"
kubectl -n "$ns" rollout status "statefulset/${cluster}" --timeout=600s

printf 'rotated RabbitMQ leaf certificate\n' > submit.txt
