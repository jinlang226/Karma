#!/bin/sh
set -eu

kubectl -n "$BENCH_NS_SOURCE" patch configmap demo-config --type=merge \
  -p="{\"data\":{\"value\":\"${BENCH_PARAM_SOURCE_VALUE:-left}\"}}"
kubectl -n "$BENCH_NS_TARGET" patch configmap demo-config --type=merge \
  -p="{\"data\":{\"value\":\"${BENCH_PARAM_TARGET_VALUE:-right}\"}}"
printf 'updated both demo ConfigMaps\n' > submit.txt
