#!/bin/sh
set -eu

kubectl -n "$BENCH_NAMESPACE" patch configmap demo-config --type=merge \
  -p="{\"data\":{\"value\":\"${BENCH_PARAM_TARGET_VALUE:-x}\"}}"
printf 'updated demo ConfigMap\n' > submit.txt
