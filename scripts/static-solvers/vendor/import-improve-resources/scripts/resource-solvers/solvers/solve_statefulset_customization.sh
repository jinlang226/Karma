#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
sts="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
label_key="${BENCH_PARAM_TEMPLATE_LABEL_KEY:-monitoring}"
label_value="${BENCH_PARAM_TEMPLATE_LABEL_VALUE:-enabled}"
request_mi="${BENCH_PARAM_MIN_REQUEST_MEMORY_MI:-512}"
limit_mi="${BENCH_PARAM_MIN_LIMIT_MEMORY_MI:-1024}"
kubectl -n "$ns" patch statefulset "$sts" --type=json -p="[
  {\"op\":\"replace\",\"path\":\"/spec/template/metadata/labels/$label_key\",\"value\":\"$label_value\"},
  {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/requests/memory\",\"value\":\"${request_mi}Mi\"},
  {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/limits/memory\",\"value\":\"${limit_mi}Mi\"}
]"
kubectl -n "$ns" rollout status "statefulset/$sts" --timeout=600s
printf 'solved statefulset customization\n' > submit.txt
