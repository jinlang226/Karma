#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
sa="${BENCH_PARAM_SERVICE_ACCOUNT_NAME:-spark-etl}"
job="${BENCH_PARAM_JOB_NAME:-etl-pipeline}"
pvc="${BENCH_PARAM_PVC_NAME:-etl-data-pvc}"
image="${BENCH_PARAM_SPARK_IMAGE:-apache/spark:3.5.3}"
mount="${BENCH_PARAM_DATA_MOUNT:-/data}"

sed -e "s/__NAMESPACE__/${ns}/g" -e "s/__PVC_NAME__/${pvc}/g" \
  resources/spark/spark_etl_pipeline_completion/resource/etl-storage.yaml |
  kubectl apply -f -
sed -e "s/__NAMESPACE__/${ns}/g" -e "s/__JOB_NAME__/${job}/g" \
  -e "s/__SERVICE_ACCOUNT__/${sa}/g" -e "s/__PVC_NAME__/${pvc}/g" \
  -e "s#__SPARK_IMAGE__#${image}#g" -e "s#__DATA_MOUNT__#${mount}#g" \
  resources/spark/spark_etl_pipeline_completion/resource/etl-job.yaml |
  kubectl apply -f -
kubectl -n "$ns" wait --for=condition=complete "job/${job}" --timeout=300s
printf 'completed Spark ETL job\n' > submit.txt
