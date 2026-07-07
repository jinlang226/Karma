#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prom_ns="$BENCH_NS_MONITORING"
mongo_service="${BENCH_PARAM_SERVICE_NAME:-mongo}"
prom_deploy="${BENCH_PARAM_PROMETHEUS_DEPLOYMENT_NAME:-prometheus}"
prom_config="${BENCH_PARAM_PROMETHEUS_CONFIGMAP_NAME:-prometheus-config}"
metrics_port="${BENCH_PARAM_METRICS_PORT:-9216}"
metrics_path="${BENCH_PARAM_METRICS_PATH:-/metrics}"

cat <<EOF | kubectl -n "$ns" apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mongodb-exporter
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mongodb-exporter
  template:
    metadata:
      labels:
        app: mongodb-exporter
    spec:
      containers:
      - name: exporter
        image: percona/mongodb_exporter:0.40.0
        args:
        - --mongodb.uri=mongodb://${mongo_service}:27017
        - --compatible-mode
        ports:
        - name: metrics
          containerPort: ${metrics_port}
---
apiVersion: v1
kind: Service
metadata:
  name: mongodb-exporter
spec:
  selector:
    app: mongodb-exporter
  ports:
  - name: metrics
    port: ${metrics_port}
    targetPort: ${metrics_port}
EOF
kubectl -n "$ns" rollout status deployment/mongodb-exporter --timeout=300s
cat <<EOF | kubectl -n "$prom_ns" apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${prom_config}
data:
  prometheus.yml: |
    global:
      scrape_interval: 5s
    scrape_configs:
    - job_name: prometheus
      static_configs:
      - targets: ["localhost:9090"]
    - job_name: mongodb
      metrics_path: ${metrics_path}
      static_configs:
      - targets: ["mongodb-exporter.${ns}.svc:${metrics_port}"]
EOF
kubectl -n "$prom_ns" rollout restart "deployment/${prom_deploy}"
kubectl -n "$prom_ns" rollout status "deployment/${prom_deploy}" --timeout=300s
printf 'configured MongoDB monitoring\n' > submit.txt
