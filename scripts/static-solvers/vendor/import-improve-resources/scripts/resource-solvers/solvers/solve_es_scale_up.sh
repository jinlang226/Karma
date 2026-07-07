#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
index="${BENCH_PARAM_INDEX_NAME:-app-data}"
expected="${BENCH_PARAM_EXPECTED_NODES:-5}"
original="${BENCH_PARAM_ORIGINAL_REPLICAS:-3}"
new_replicas=$((expected - original))
nodeset="$prefix-warm"
image=$(kubectl -n "$ns" get "statefulset/$prefix" \
  -o jsonpath='{.spec.template.spec.containers[0].image}')
[ -n "$image" ]

cat <<YAML | kubectl -n "$ns" apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: $nodeset-config
data:
  elasticsearch.yml: |
    cluster.name: $prefix
    node.name: \${POD_NAME}
    node.roles: [ data, ingest ]
    node.attr.tier: warm
    network.host: 0.0.0.0
    discovery.seed_hosts: [ "$prefix" ]
    node.store.allow_mmap: false
    xpack.security.enabled: false
    xpack.security.http.ssl.enabled: false
    xpack.security.transport.ssl.enabled: false
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: $nodeset
spec:
  serviceName: $prefix
  podManagementPolicy: Parallel
  replicas: $new_replicas
  selector:
    matchLabels:
      app: $nodeset
  template:
    metadata:
      labels:
        app: $nodeset
    spec:
      containers:
      - name: elasticsearch
        image: $image
        env:
        - name: POD_NAME
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - {name: ES_JAVA_OPTS, value: "-Xms512m -Xmx512m"}
        ports:
        - {name: http, containerPort: 9200}
        - {name: transport, containerPort: 9300}
        readinessProbe:
          tcpSocket: {port: http}
          initialDelaySeconds: 10
          periodSeconds: 5
        volumeMounts:
        - {name: config, mountPath: /usr/share/elasticsearch/config/elasticsearch.yml, subPath: elasticsearch.yml}
        - {name: data, mountPath: /usr/share/elasticsearch/data}
        resources:
          requests: {cpu: 300m, memory: 1Gi}
          limits: {cpu: "1", memory: 2Gi}
      volumes:
      - name: config
        configMap:
          name: $nodeset-config
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: [ReadWriteOnce]
      resources:
        requests:
          storage: 2Gi
YAML
kubectl -n "$ns" rollout status "statefulset/$nodeset" --timeout=900s
kubectl -n "$ns" exec curl-test -- curl -fsS -XPUT \
  "http://$service:9200/$index/_settings" \
  -H 'Content-Type: application/json' \
  -d '{"index.routing.allocation.require.tier":"warm"}' >/dev/null
relocated=false
for _ in $(seq 1 120); do
  if kubectl -n "$ns" exec curl-test -- curl -fsS \
    "http://$service:9200/_cat/shards/$index?format=json" |
    python3 -c '
import json, sys
shards = json.load(sys.stdin)
raise SystemExit(0 if any("-warm-" in str(s.get("node") or "") for s in shards) else 1)
'; then
    relocated=true
    break
  fi
  sleep 3
done
[ "$relocated" = "true" ]
kubectl -n "$ns" exec curl-test -- curl -fsS \
  "http://$service:9200/_cluster/health?wait_for_status=yellow&wait_for_nodes=$expected&timeout=60s" \
  >/dev/null
printf 'added a separate warm nodeset and moved shards\n' > submit.txt
