#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
readiness_delay="${BENCH_PARAM_TUNED_READINESS_INITIAL_DELAY:-20}"
readiness_timeout="${BENCH_PARAM_TUNED_READINESS_TIMEOUT:-5}"
readiness_failures="${BENCH_PARAM_TUNED_READINESS_FAILURE_THRESHOLD:-6}"
liveness_delay="${BENCH_PARAM_TUNED_LIVENESS_INITIAL_DELAY:-120}"
liveness_timeout="${BENCH_PARAM_TUNED_LIVENESS_TIMEOUT:-5}"
liveness_failures="${BENCH_PARAM_TUNED_LIVENESS_FAILURE_THRESHOLD:-10}"
kubectl -n "$ns" patch "statefulset/${cluster}" --type=json -p="[
  {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/readinessProbe/initialDelaySeconds\",\"value\":${readiness_delay}},
  {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/readinessProbe/timeoutSeconds\",\"value\":${readiness_timeout}},
  {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/readinessProbe/failureThreshold\",\"value\":${readiness_failures}},
  {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/livenessProbe/initialDelaySeconds\",\"value\":${liveness_delay}},
  {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/livenessProbe/timeoutSeconds\",\"value\":${liveness_timeout}},
  {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/livenessProbe/failureThreshold\",\"value\":${liveness_failures}}
]"
kubectl -n "$ns" rollout status "statefulset/${cluster}" --timeout=600s
printf 'tuned MongoDB probes\n' > submit.txt
