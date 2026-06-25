#!/bin/sh
set -eu

ns="$BENCH_NS_APP"
host="${BENCH_PARAM_HOST:-demo.example.com}"
path="${BENCH_PARAM_PATH:-/}"
service="${BENCH_PARAM_SERVICE_NAME:-echo-server}"
ingress="${BENCH_PARAM_INGRESS_NAME:-demo-ingress}"
secret="${BENCH_PARAM_TLS_SECRET_NAME:-demo-tls}"
class="${BENCH_PARAM_INGRESS_CLASS_NAME:-nginx}"
min_seconds="${BENCH_PARAM_MIN_VALIDITY_SECONDS:-86400}"
days=$(( (min_seconds + 86399) / 86400 + 1 ))
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout "$tmp/tls.key" -out "$tmp/tls.crt" -days "$days" \
  -subj "/CN=${host}" -addext "subjectAltName = DNS:${host}" >/dev/null 2>&1
kubectl -n "$ns" create secret tls "$secret" \
  --cert="$tmp/tls.crt" --key="$tmp/tls.key" \
  --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
kubectl -n "$ns" apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${ingress}
spec:
  ingressClassName: ${class}
  tls:
  - hosts:
    - ${host}
    secretName: ${secret}
  rules:
  - host: ${host}
    http:
      paths:
      - path: ${path}
        pathType: Prefix
        backend:
          service:
            name: ${service}
            port:
              number: 5678
EOF
printf 'created HTTPS ingress\n' > submit.txt
