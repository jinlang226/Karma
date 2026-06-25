#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
sa="${BENCH_PARAM_SERVICE_ACCOUNT_NAME:-spark-runtime}"
config="${BENCH_PARAM_CONFIGMAP_NAME:-spark-config}"
secret="${BENCH_PARAM_SECRET_NAME:-spark-credentials}"
monitor="${BENCH_PARAM_MONITOR_DEPLOYMENT_NAME:-spark-monitor}"
job="${BENCH_PARAM_JOB_NAME:-spark-batch-processor}"
image="${BENCH_PARAM_SPARK_IMAGE:-apache/spark:3.5.3}"
executor_memory="${BENCH_PARAM_EXECUTOR_MEMORY:-512m}"
driver_memory="${BENCH_PARAM_DRIVER_MEMORY:-512m}"
api_key="${BENCH_PARAM_API_KEY:-sk-valid-key-12345}"

sed -e "s/__NAMESPACE__/${ns}/g" -e "s/__CONFIGMAP_NAME__/${config}/g" \
  -e "s/__EXECUTOR_MEMORY__/${executor_memory}/g" \
  -e "s/__DRIVER_MEMORY__/${driver_memory}/g" \
  resources/spark/spark_runtime_bundle_ready/resource/spark-config.yaml |
  kubectl apply -f -
sed -e "s/__NAMESPACE__/${ns}/g" -e "s/__SECRET_NAME__/${secret}/g" \
  -e "s/__API_KEY__/${api_key}/g" \
  resources/spark/spark_runtime_bundle_ready/resource/spark-credentials.yaml |
  kubectl apply -f -
sed -e "s/__NAMESPACE__/${ns}/g" -e "s/__DEPLOYMENT_NAME__/${monitor}/g" \
  -e "s/__SERVICE_ACCOUNT__/${sa}/g" -e "s#__SPARK_IMAGE__#${image}#g" \
  resources/spark/spark_runtime_bundle_ready/resource/spark-monitor.yaml |
  kubectl apply -f -
sed -e "s/__NAMESPACE__/${ns}/g" -e "s/__JOB_NAME__/${job}/g" \
  -e "s/__SERVICE_ACCOUNT__/${sa}/g" -e "s#__SPARK_IMAGE__#${image}#g" \
  -e "s/__CONFIGMAP_NAME__/${config}/g" -e "s/__SECRET_NAME__/${secret}/g" \
  resources/spark/spark_runtime_bundle_ready/resource/spark-batch-job.yaml |
  kubectl apply -f -
kubectl -n "$ns" rollout status "deployment/${monitor}" --timeout=300s
kubectl -n "$ns" wait --for=condition=complete "job/${job}" --timeout=300s
printf 'completed Spark runtime bundle\n' > submit.txt
