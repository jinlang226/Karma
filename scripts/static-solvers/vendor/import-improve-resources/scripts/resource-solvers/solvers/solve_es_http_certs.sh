#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
secret="${BENCH_PARAM_TLS_SECRET_NAME:-es-http-tls}"
ca_cm="${BENCH_PARAM_HTTP_CA_CONFIGMAP_NAME:-es-http-ca}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

kubectl -n "$ns" exec openssl-toolbox -- /bin/sh -c "
set -e
rm -rf /tmp/rotated && mkdir -p /tmp/rotated
cat > /tmp/rotated/openssl.cnf <<EOF
distinguished_name=dn
req_extensions=v3_req
prompt=no
[dn]
CN=$service
[v3_req]
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt_names
[alt_names]
DNS.1=localhost
DNS.2=$service
DNS.3=$prefix
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
kubectl -n "$ns" cp openssl-toolbox:/tmp/rotated "$tmp/rotated"
kubectl -n "$ns" create secret generic "$secret" \
  --from-file=tls.crt="$tmp/rotated/tls.crt" \
  --from-file=tls.key="$tmp/rotated/tls.key" \
  --from-file=ca.crt="$tmp/rotated/ca.crt" \
  --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
kubectl -n "$ns" create configmap "$ca_cm" --from-file=ca.crt="$tmp/rotated/ca.crt" \
  --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
kubectl -n "$ns" rollout restart "statefulset/$prefix"
kubectl -n "$ns" rollout status "statefulset/$prefix" --timeout=900s
kubectl -n "$ns" delete pod curl-test --ignore-not-found=true --wait=true
envsubst '${BENCH_PARAM_HTTP_CA_CONFIGMAP_NAME}' \
  < resources/elasticsearch/rotate-http-certs/resource/curl-test.yaml | kubectl -n "$ns" apply -f -
kubectl -n "$ns" wait --for=condition=ready pod/curl-test --timeout=300s
printf 'rotated HTTP CA and leaf certificate\n' > submit.txt
