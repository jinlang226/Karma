#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
to="${BENCH_PARAM_TO_VERSION:-24.1.0}"
kubectl -n "$ns" set image "statefulset/${prefix}" "db=cockroachdb/cockroach:v${to}"
kubectl -n "$ns" rollout status "statefulset/${prefix}" --timeout=1200s
kubectl -n "$ns" exec "${prefix}-0" -- ./cockroach sql --insecure -e \
  "RESET CLUSTER SETTING cluster.preserve_downgrade_option;"

target_family=$(printf '%s' "$to" | cut -d. -f1,2)
for i in $(seq 1 120); do
  version=$(kubectl -n "$ns" exec "${prefix}-0" -- ./cockroach sql --insecure \
    --format=tsv -e "SHOW CLUSTER SETTING version;" 2>/dev/null | tail -n1)
  if [ "$version" = "$target_family" ] || [ "$version" = "$to" ]; then
    printf 'upgraded and finalized CockroachDB\n' > submit.txt
    exit 0
  fi
  sleep 5
done

exit 1
