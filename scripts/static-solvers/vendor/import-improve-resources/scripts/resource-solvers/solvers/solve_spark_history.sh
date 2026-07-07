#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
sa="${BENCH_PARAM_SERVICE_ACCOUNT_NAME:-spark-history}"
deployment="${BENCH_PARAM_DEPLOYMENT_NAME:-spark-history-server}"
service="${BENCH_PARAM_SERVICE_NAME:-spark-history-server}"
pvc="${BENCH_PARAM_PVC_NAME:-spark-history-pvc}"
image="${BENCH_PARAM_SPARK_IMAGE:-apache/spark:3.5.3}"
log_dir="${BENCH_PARAM_LOG_DIR:-/mnt/spark-logs}"
port="${BENCH_PARAM_SERVICE_PORT:-18080}"
replicas="${BENCH_PARAM_SERVER_REPLICAS:-1}"

sed -e "s/__NAMESPACE__/${ns}/g" -e "s/__PVC_NAME__/${pvc}/g" \
  resources/spark/spark_history_server_ready/resource/history-storage.yaml |
  kubectl apply -f -
sed -e "s/__NAMESPACE__/${ns}/g" -e "s/__SERVICE_NAME__/${service}/g" \
  -e "s/__DEPLOYMENT_NAME__/${deployment}/g" -e "s/__SERVICE_PORT__/${port}/g" \
  resources/spark/spark_history_server_ready/resource/history-service.yaml |
  kubectl apply -f -
sed -e "s/__NAMESPACE__/${ns}/g" -e "s/__DEPLOYMENT_NAME__/${deployment}/g" \
  -e "s/__SERVICE_ACCOUNT__/${sa}/g" -e "s#__SPARK_IMAGE__#${image}#g" \
  -e "s#__LOG_DIR__#${log_dir}#g" -e "s/__PVC_NAME__/${pvc}/g" \
  -e "s/__SERVICE_PORT__/${port}/g" -e "s/__SERVER_REPLICAS__/${replicas}/g" \
  resources/spark/spark_history_server_ready/resource/history-deployment.yaml |
  kubectl apply -f -
kubectl -n "$ns" rollout status "deployment/${deployment}" --timeout=300s
printf 'started Spark History Server\n' > submit.txt
