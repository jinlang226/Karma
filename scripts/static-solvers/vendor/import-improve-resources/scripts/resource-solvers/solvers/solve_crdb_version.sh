#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
version=$(kubectl -n "$ns" exec "${prefix}-0" -- ./cockroach sql --insecure \
  --format=tsv -e 'SHOW CLUSTER SETTING version;' | tail -n1)
kubectl -n "$ns" create configmap crdb-version-report \
  --from-literal=db_version="$version" --dry-run=client -o yaml |
  kubectl -n "$ns" apply -f -
printf 'recorded CockroachDB active feature version\n' > submit.txt
