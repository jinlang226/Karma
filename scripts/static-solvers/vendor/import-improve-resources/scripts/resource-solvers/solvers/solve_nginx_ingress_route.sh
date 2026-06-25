#!/bin/sh
set -eu

app_ns="$BENCH_NS_APP"
service="${BENCH_PARAM_SERVICE_NAME:-demo-app}"
ingress="${BENCH_PARAM_INGRESS_NAME:-demo-route}"
host="${BENCH_PARAM_HOST:-demo.example.com}"
path="${BENCH_PARAM_PATH:-/}"
class="${BENCH_PARAM_INGRESS_CLASS_NAME:-nginx}"
service_port="${BENCH_PARAM_SERVICE_PORT:-80}"
target_port="${BENCH_PARAM_TARGET_PORT:-5678}"

cat <<EOF | kubectl -n "$app_ns" apply -f -
apiVersion: v1
kind: Service
metadata:
  name: ${service}
spec:
  selector:
    app: demo-app
  ports:
  - name: http
    port: ${service_port}
    targetPort: ${target_port}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${ingress}
spec:
  ingressClassName: ${class}
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
              number: ${service_port}
EOF

printf 'created backend service and ingress route\n' > submit.txt
