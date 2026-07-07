#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
replicas="${BENCH_PARAM_REPLICA_COUNT:-3}"
join_hosts=""
for ordinal in $(seq 0 $((replicas - 1))); do
  host="${prefix}-${ordinal}.${prefix}.${ns}.svc.cluster.local:26257"
  if [ -n "$join_hosts" ]; then
    join_hosts="${join_hosts},${host}"
  else
    join_hosts="$host"
  fi
done
envsubst '${BENCH_PARAM_CLUSTER_PREFIX} ${BENCH_PARAM_REPLICA_COUNT}' \
  < resources/cockroachdb/initialize/resource/statefulset.yaml |
  sed "s#--join=${prefix}#--join=${join_hosts}#" |
  kubectl -n "$ns" apply -f -
kubectl -n "$ns" delete pod -l "app.kubernetes.io/instance=${prefix}" --wait=false
kubectl -n "$ns" wait --for=jsonpath='{.status.phase}'=Running \
  "pod/${prefix}-0" --timeout=300s
initialized=false
for i in $(seq 1 60); do
  out=$(kubectl -n "$ns" exec "${prefix}-0" -- ./cockroach init --insecure \
    --host="${prefix}-0.${prefix}.${ns}.svc.cluster.local" 2>&1) && initialized=true && break
  printf '%s' "$out" | grep -qi 'already been initialized' && initialized=true && break
  sleep 2
done
[ "$initialized" = "true" ]
kubectl -n "$ns" wait --for=condition=ready pod \
  -l "app.kubernetes.io/instance=${prefix}" --timeout=900s
printf 'initialized CockroachDB cluster\n' > submit.txt
