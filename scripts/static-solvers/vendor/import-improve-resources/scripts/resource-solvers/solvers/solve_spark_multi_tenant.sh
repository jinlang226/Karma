#!/bin/sh
set -eu

count="${BENCH_PARAM_TENANT_COUNT:-2}"
sa_prefix="${BENCH_PARAM_SERVICE_ACCOUNT_PREFIX:-spark-team}"
job_prefix="${BENCH_PARAM_JOB_NAME_PREFIX:-spark-pi-team}"
image="${BENCH_PARAM_SPARK_IMAGE:-apache/spark:3.5.3}"
memory="${BENCH_PARAM_DRIVER_MEMORY:-512m}"
iterations="${BENCH_PARAM_ITERATIONS:-1000}"
index=0

for role in team_a team_b team_c team_d; do
  index=$((index + 1))
  [ "$index" -le "$count" ] || break
  case "$role" in
    team_a) ns="$BENCH_NS_TEAM_A"; suffix=a; label=team-a ;;
    team_b) ns="$BENCH_NS_TEAM_B"; suffix=b; label=team-b ;;
    team_c) ns="$BENCH_NS_TEAM_C"; suffix=c; label=team-c ;;
    team_d) ns="$BENCH_NS_TEAM_D"; suffix=d; label=team-d ;;
  esac
  job="${job_prefix}-${suffix}"
  sed -e "s/__NAMESPACE__/${ns}/g" -e "s/__JOB_NAME__/${job}/g" \
    -e "s/__SERVICE_ACCOUNT__/${sa_prefix}-${suffix}/g" \
    -e "s#__SPARK_IMAGE__#${image}#g" -e "s/__DRIVER_MEMORY__/${memory}/g" \
    -e "s/__ITERATIONS__/${iterations}/g" -e "s/__TENANT_LABEL__/${label}/g" \
    resources/spark/spark_multi_tenant_job_execution/resource/spark-pi-job.yaml |
    kubectl apply -f -
  kubectl -n "$ns" wait --for=condition=complete "job/${job}" --timeout=300s
done
printf 'completed tenant SparkPi jobs\n' > submit.txt
