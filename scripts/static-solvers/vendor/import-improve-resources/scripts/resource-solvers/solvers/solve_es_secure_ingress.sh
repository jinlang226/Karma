#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
host="${BENCH_PARAM_INGRESS_HOST:-es.example.com}"
class="${BENCH_PARAM_INGRESS_CLASS_NAME:-nginx}"
secret="${BENCH_PARAM_TLS_SECRET_NAME:-es-http-tls}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

kubectl -n "$ns" exec openssl-toolbox -- /bin/sh -c "
set -e
rm -rf /tmp/secure-ingress && mkdir -p /tmp/secure-ingress
cat > /tmp/secure-ingress/openssl.cnf <<EOF
distinguished_name=dn
req_extensions=v3_req
prompt=no
[dn]
CN=$host
[v3_req]
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=@alt_names
[alt_names]
DNS.1=$host
DNS.2=$service
DNS.3=$prefix
DNS.4=*.svc
DNS.5=*.svc.cluster.local
EOF
openssl genrsa -out /tmp/secure-ingress/ca.key 2048
openssl req -x509 -new -nodes -key /tmp/secure-ingress/ca.key -sha256 -days 365 \
  -subj '/CN=es-secure-ingress-ca' -out /tmp/secure-ingress/ca.crt
openssl genrsa -out /tmp/secure-ingress/tls.key 2048
openssl req -new -key /tmp/secure-ingress/tls.key \
  -out /tmp/secure-ingress/tls.csr -config /tmp/secure-ingress/openssl.cnf
openssl x509 -req -in /tmp/secure-ingress/tls.csr \
  -CA /tmp/secure-ingress/ca.crt -CAkey /tmp/secure-ingress/ca.key \
  -CAcreateserial -out /tmp/secure-ingress/tls.crt -days 365 \
  -extensions v3_req -extfile /tmp/secure-ingress/openssl.cnf
"
kubectl -n "$ns" cp openssl-toolbox:/tmp/secure-ingress "$tmp/certs"
kubectl -n "$ns" create secret generic "$secret" \
  --from-file=tls.crt="$tmp/certs/tls.crt" \
  --from-file=tls.key="$tmp/certs/tls.key" \
  --from-file=ca.crt="$tmp/certs/ca.crt" \
  --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
cat <<YAML | kubectl -n "$ns" apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: es-config
data:
  elasticsearch.yml: |
    cluster.name: $prefix
    node.name: \${POD_NAME}
    node.roles: [ master, data, ingest ]
    network.host: 0.0.0.0
    discovery.seed_hosts: [ "$prefix" ]
    node.store.allow_mmap: false
    xpack.security.enabled: true
    xpack.security.http.ssl.enabled: true
    xpack.security.http.ssl.key: http-certs/tls.key
    xpack.security.http.ssl.certificate: http-certs/tls.crt
    xpack.security.http.ssl.certificate_authorities: [ "http-certs/ca.crt" ]
    xpack.security.transport.ssl.enabled: false
YAML
kubectl -n "$ns" patch "statefulset/$prefix" --type=strategic -p "
spec:
  template:
    spec:
      containers:
      - name: elasticsearch
        env:
        - name: ELASTIC_PASSWORD
          value: elasticpass
        volumeMounts:
        - name: http-certs
          mountPath: /usr/share/elasticsearch/config/http-certs
          readOnly: true
      volumes:
      - name: http-certs
        secret:
          secretName: $secret
"
kubectl -n "$ns" rollout status "statefulset/$prefix" --timeout=900s
cat <<YAML | kubectl -n "$ns" apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: es-secure-$secret
  annotations:
    nginx.ingress.kubernetes.io/backend-protocol: HTTPS
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
spec:
  ingressClassName: $class
  tls:
  - hosts: [$host]
    secretName: $secret
  rules:
  - host: $host
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: $service
            port:
              number: 9200
YAML
printf 'enabled HTTPS and exposed it through ingress\n' > submit.txt
