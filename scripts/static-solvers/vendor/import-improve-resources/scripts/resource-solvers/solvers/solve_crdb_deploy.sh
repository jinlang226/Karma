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
envsubst '${BENCH_PARAM_CLUSTER_PREFIX}' \
  < resources/cockroachdb/deploy/resource/services.yaml | kubectl -n "$ns" apply -f -
envsubst '${BENCH_PARAM_CLUSTER_PREFIX} ${BENCH_PARAM_REPLICA_COUNT} ${BENCH_PARAM_STORAGE_SIZE_GI} ${BENCH_PARAM_TO_VERSION}' \
  < resources/cockroachdb/deploy/resource/statefulset.yaml |
  sed "s#--join=${prefix}#--join=${join_hosts}#" |
  kubectl -n "$ns" apply -f -
min_available=$((replicas - 1))
cat <<EOF | kubectl -n "$ns" apply -f -
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: ${prefix}-pdb
  labels:
    app.kubernetes.io/name: cockroachdb
    app.kubernetes.io/instance: ${prefix}
spec:
  minAvailable: ${min_available}
  selector:
    matchLabels:
      app.kubernetes.io/name: cockroachdb
      app.kubernetes.io/instance: ${prefix}
EOF

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
[ "$(kubectl -n "$ns" get sts "$prefix" -o jsonpath='{.status.readyReplicas}')" = "$replicas" ]
printf 'deployed CockroachDB cluster\n' > submit.txt
