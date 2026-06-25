#!/bin/sh
set -eu

kubectl -n "$BENCH_NS_APP" patch ingress \
  "${BENCH_PARAM_INGRESS_NAME:-demo-app}" --type=merge \
  -p="{\"spec\":{\"ingressClassName\":\"${BENCH_PARAM_INGRESS_CLASS_NAME:-nginx}\"}}"
printf 'set ingress class\n' > submit.txt
