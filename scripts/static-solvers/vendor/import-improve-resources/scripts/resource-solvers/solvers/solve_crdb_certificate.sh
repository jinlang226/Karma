#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
secret="${BENCH_PARAM_CERT_SECRET_NAME:-crdb-cluster-certs}"
days="${BENCH_PARAM_MIN_ROTATED_VALIDITY_DAYS:-300}"
days=$((days + 30))
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
kubectl -n "$ns" exec openssl-toolbox -- sh -c "
set -e
cd /tmp/certs
cat > node-rotated.cnf <<EOF
[req]
distinguished_name=dn
req_extensions=v3_req
prompt=no
[dn]
CN=node
[v3_req]
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt
[alt]
DNS.1=localhost
IP.1=127.0.0.1
DNS.2=${prefix}
DNS.3=${prefix}.${ns}
DNS.4=${prefix}.${ns}.svc
DNS.5=${prefix}.${ns}.svc.cluster.local
DNS.6=*.${prefix}.${ns}.svc.cluster.local
EOF
openssl genrsa -out node-new.key 2048
openssl req -new -key node-new.key -out node-new.csr -config node-rotated.cnf
openssl x509 -req -in node-new.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out node-new.crt -days ${days} -extensions v3_req -extfile node-rotated.cnf
openssl genrsa -out client.root-new.key 2048
openssl req -new -key client.root-new.key -subj '/CN=root' -out client.root-new.csr
openssl x509 -req -in client.root-new.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out client.root-new.crt -days ${days}
"
kubectl -n "$ns" cp openssl-toolbox:/tmp/certs "$tmp/certs"
kubectl -n "$ns" create secret generic "$secret" \
  --from-file=ca.crt="$tmp/certs/ca.crt" \
  --from-file=node.crt="$tmp/certs/node-new.crt" \
  --from-file=node.key="$tmp/certs/node-new.key" \
  --from-file=client.root.crt="$tmp/certs/client.root-new.crt" \
  --from-file=client.root.key="$tmp/certs/client.root-new.key" \
  --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
kubectl -n "$ns" rollout restart "statefulset/${prefix}"
kubectl -n "$ns" rollout status "statefulset/${prefix}" --timeout=900s
printf 'rotated CockroachDB certificates\n' > submit.txt
