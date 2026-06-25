#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
ingress_ns="$BENCH_NS_INGRESS"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
host="${BENCH_PARAM_UI_HOST:-crdb-ui.example.com}"
class="${BENCH_PARAM_INGRESS_CLASS_NAME:-nginx}"
secret="${BENCH_PARAM_TLS_SECRET_NAME:-crdb-ui-tls}"
sql_port="${BENCH_PARAM_SQL_PORT:-26257}"
cat <<EOF | kubectl -n "$ns" apply -f -
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${prefix}-ui
spec:
  ingressClassName: ${class}
  tls:
  - hosts: [${host}]
    secretName: ${secret}
  rules:
  - host: ${host}
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: ${prefix}-public
            port:
              number: 8080
EOF
kubectl -n "$ingress_ns" create configmap tcp-services \
  --from-literal="${sql_port}=${ns}/${prefix}-public:26257" \
  --dry-run=client -o yaml | kubectl -n "$ingress_ns" apply -f -
kubectl -n "$ingress_ns" patch deployment ingress-nginx-controller --type=json \
  -p="[{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/args/-\",\"value\":\"--tcp-services-configmap=${ingress_ns}/tcp-services\"}]"
kubectl -n "$ingress_ns" patch service ingress-nginx-controller --type=merge \
  -p="{\"spec\":{\"ports\":[{\"name\":\"http\",\"port\":80,\"protocol\":\"TCP\",\"targetPort\":\"http\"},{\"name\":\"https\",\"port\":443,\"protocol\":\"TCP\",\"targetPort\":\"https\"},{\"name\":\"sql-${sql_port}\",\"port\":${sql_port},\"protocol\":\"TCP\",\"targetPort\":${sql_port}}]}}"
kubectl -n "$ingress_ns" rollout status deployment/ingress-nginx-controller --timeout=300s
printf 'exposed CockroachDB through ingress-nginx\n' > submit.txt
