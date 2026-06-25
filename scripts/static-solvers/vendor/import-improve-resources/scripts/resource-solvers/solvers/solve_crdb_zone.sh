#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
schema="${BENCH_PARAM_TARGET_SCHEMA:-tenant_b}"
replicas="${BENCH_PARAM_NUM_REPLICAS:-3}"
ttl="${BENCH_PARAM_GC_TTL_SECONDS:-14400}"
min="${BENCH_PARAM_RANGE_MIN_BYTES:-134217728}"
max="${BENCH_PARAM_RANGE_MAX_BYTES:-536870912}"
tables=$(kubectl -n "$ns" exec "${prefix}-0" -- ./cockroach sql --insecure \
  --database=defaultdb --format=tsv -e \
  "SELECT table_name FROM information_schema.tables WHERE table_schema='${schema}' AND table_type='BASE TABLE';" |
  tail -n +2)
for table in $tables; do
  kubectl -n "$ns" exec "${prefix}-0" -- ./cockroach sql --insecure \
    --database=defaultdb -e \
    "ALTER TABLE ${schema}.${table} CONFIGURE ZONE USING num_replicas=${replicas}, gc.ttlseconds=${ttl}, range_min_bytes=${min}, range_max_bytes=${max};"
done
printf 'configured CockroachDB tenant zones\n' > submit.txt
