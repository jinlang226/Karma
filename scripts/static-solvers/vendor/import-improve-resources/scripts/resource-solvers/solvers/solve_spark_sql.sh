#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
sa="${BENCH_PARAM_SERVICE_ACCOUNT_NAME:-spark-sql}"
job="${BENCH_PARAM_JOB_NAME:-spark-sql-job}"
image="${BENCH_PARAM_SPARK_IMAGE:-apache/spark:3.5.3}"

sed -e "s/__NAMESPACE__/${ns}/g" -e "s/__JOB_NAME__/${job}/g" \
  -e "s/__SERVICE_ACCOUNT__/${sa}/g" -e "s#__SPARK_IMAGE__#${image}#g" \
  resources/spark/spark_sql_job_execution/resource/sql-job.yaml |
  kubectl apply -f -
kubectl -n "$ns" wait --for=condition=complete "job/${job}" --timeout=300s
printf 'completed Spark SQL job\n' > submit.txt
