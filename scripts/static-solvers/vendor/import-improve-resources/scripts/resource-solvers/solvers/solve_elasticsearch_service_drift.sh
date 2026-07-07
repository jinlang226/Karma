#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
service="${BENCH_PARAM_PROD_SERVICE_NAME:-search-http}"
prod="${BENCH_PARAM_PROD_CLUSTER_PREFIX:-es-alpha}"
reader="${BENCH_PARAM_LOG_READER_DEPLOYMENT:-log-reader}"
kubectl -n "$ns" patch service "$service" --type=merge \
  -p="{\"spec\":{\"selector\":{\"app\":\"${prod}\"}}}"
kubectl -n "$ns" rollout status "deployment/${reader}" --timeout=300s
printf 'reconciled Elasticsearch production service selector\n' > submit.txt
