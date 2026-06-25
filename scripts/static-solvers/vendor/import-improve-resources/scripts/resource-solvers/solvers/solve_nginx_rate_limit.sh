#!/bin/sh
set -eu

ingress_ns="$BENCH_NS_INGRESS"
app_ns="$BENCH_NS_APP"
api="${BENCH_PARAM_API_INGRESS_NAME:-rate-api}"
status="${BENCH_PARAM_LIMIT_STATUS_CODE:-429}"

kubectl -n "$ingress_ns" patch configmap ingress-nginx-controller --type=merge \
  -p="{\"data\":{\"limit-req-status-code\":\"${status}\"}}"
kubectl -n "$ingress_ns" patch service ingress-nginx-controller --type=merge \
  -p='{"spec":{"sessionAffinity":"ClientIP","sessionAffinityConfig":{"clientIP":{"timeoutSeconds":10800}}}}'
kubectl -n "$app_ns" annotate ingress "$api" \
  nginx.ingress.kubernetes.io/limit-rps="1" \
  nginx.ingress.kubernetes.io/limit-burst-multiplier="1" \
  --overwrite
kubectl -n "$ingress_ns" rollout restart deployment/ingress-nginx-controller
kubectl -n "$ingress_ns" rollout status deployment/ingress-nginx-controller --timeout=180s
printf 'enabled API rate limiting\n' > submit.txt
