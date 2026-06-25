#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
sa="${BENCH_PARAM_SERVICE_ACCOUNT_NAME:-spark}"
job="${BENCH_PARAM_JOB_NAME:-spark-pi}"
image="${BENCH_PARAM_SPARK_IMAGE:-apache/spark:3.5.3}"
memory="${BENCH_PARAM_DRIVER_MEMORY:-512m}"
iterations="${BENCH_PARAM_ITERATIONS:-1000}"

sed -e "s/__NAMESPACE__/${ns}/g" -e "s/__JOB_NAME__/${job}/g" \
  -e "s/__SERVICE_ACCOUNT__/${sa}/g" -e "s#__SPARK_IMAGE__#${image}#g" \
  -e "s/__DRIVER_MEMORY__/${memory}/g" -e "s/__ITERATIONS__/${iterations}/g" \
  resources/spark/spark_pi_job_execution/resource/spark-pi-job.yaml |
  kubectl apply -f -
kubectl -n "$ns" wait --for=condition=complete "job/${job}" --timeout=300s
printf 'completed SparkPi job\n' > submit.txt
