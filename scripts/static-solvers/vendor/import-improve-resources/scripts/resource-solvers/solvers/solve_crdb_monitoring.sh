#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
path="/_status/vars"
port="8080"
cat <<EOF | kubectl -n "$ns" apply -f -
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: crdb-monitor
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: cockroachdb
      app.kubernetes.io/instance: ${prefix}
  endpoints:
  - port: http
    path: ${path}
    interval: 5s
EOF
sleep 15
printf 'configured CockroachDB monitoring on port %s\n' "$port" > submit.txt
