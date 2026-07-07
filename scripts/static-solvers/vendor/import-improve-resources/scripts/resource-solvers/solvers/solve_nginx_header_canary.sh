#!/bin/sh
set -eu

ns="$BENCH_NS_APP"
ingress="${BENCH_PARAM_CANARY_INGRESS_NAME:-canary-canary}"
header="${BENCH_PARAM_HEADER_NAME:-X-Canary}"
value="${BENCH_PARAM_HEADER_VALUE:-always}"
kubectl -n "$ns" annotate ingress "$ingress" \
  nginx.ingress.kubernetes.io/canary=true \
  "nginx.ingress.kubernetes.io/canary-by-header=${header}" \
  "nginx.ingress.kubernetes.io/canary-by-header-value=${value}" \
  --overwrite
printf 'fixed header canary routing\n' > submit.txt
