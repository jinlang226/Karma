#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
tls="${BENCH_PARAM_TLS_SECRET_NAME:-es-http-tls}"
password_secret="${BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME:-elastic-password}"
password_key="${BENCH_PARAM_ELASTIC_PASSWORD_KEY:-password}"
replicas="${BENCH_PARAM_EXPECTED_NODES:-3}"
image="${BENCH_PARAM_TARGET_IMAGE:-docker.elastic.co/elasticsearch/elasticsearch:8.11.1}"
initial_nodes=
ordinal=0
while [ "$ordinal" -lt "$replicas" ]; do
  [ -z "$initial_nodes" ] || initial_nodes="$initial_nodes,"
  initial_nodes="$initial_nodes$prefix-$ordinal"
  ordinal=$((ordinal + 1))
done

cat <<YAML | kubectl -n "$ns" apply -f -
apiVersion: v1
kind: ConfigMap
metadata:
  name: es-config
data:
  elasticsearch.yml: |
    cluster.name: $prefix
    node.name: \${POD_NAME}
    node.roles: [ master, data, ingest ]
    network.host: 0.0.0.0
    discovery.seed_hosts: [ "$prefix" ]
    cluster.initial_master_nodes: [ $initial_nodes ]
    node.store.allow_mmap: false
    xpack.security.enabled: true
    xpack.security.enrollment.enabled: false
    xpack.security.http.ssl.enabled: true
    xpack.security.http.ssl.key: http-certs/tls.key
    xpack.security.http.ssl.certificate: http-certs/tls.crt
    xpack.security.http.ssl.certificate_authorities: [ "http-certs/ca.crt" ]
    xpack.security.transport.ssl.enabled: true
    xpack.security.transport.ssl.verification_mode: certificate
    xpack.security.transport.ssl.key: http-certs/tls.key
    xpack.security.transport.ssl.certificate: http-certs/tls.crt
    xpack.security.transport.ssl.certificate_authorities: [ "http-certs/ca.crt" ]
---
apiVersion: v1
kind: Service
metadata:
  name: $prefix
spec:
  clusterIP: None
  publishNotReadyAddresses: true
  selector:
    app: $prefix
  ports:
  - {name: transport, port: 9300, targetPort: transport}
---
apiVersion: v1
kind: Service
metadata:
  name: $service
spec:
  selector:
    app: $prefix
  ports:
  - {name: http, port: 9200, targetPort: http}
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: $prefix
spec:
  serviceName: $prefix
  podManagementPolicy: Parallel
  replicas: $replicas
  selector:
    matchLabels:
      app: $prefix
  template:
    metadata:
      labels:
        app: $prefix
    spec:
      securityContext:
        fsGroup: 1000
      containers:
      - name: elasticsearch
        image: $image
        env:
        - name: POD_NAME
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: ES_JAVA_OPTS
          value: -Xms512m -Xmx512m
        - name: ELASTIC_PASSWORD
          valueFrom:
            secretKeyRef:
              name: $password_secret
              key: $password_key
        ports:
        - {name: http, containerPort: 9200}
        - {name: transport, containerPort: 9300}
        readinessProbe:
          tcpSocket:
            port: http
          initialDelaySeconds: 10
          periodSeconds: 5
          failureThreshold: 12
        volumeMounts:
        - {name: es-config, mountPath: /usr/share/elasticsearch/config/elasticsearch.yml, subPath: elasticsearch.yml}
        - {name: http-certs, mountPath: /usr/share/elasticsearch/config/http-certs, readOnly: true}
        - {name: data, mountPath: /usr/share/elasticsearch/data}
        resources:
          requests: {cpu: 300m, memory: 1Gi}
          limits: {cpu: "1", memory: 2Gi}
      volumes:
      - name: es-config
        configMap:
          name: es-config
      - name: http-certs
        secret:
          secretName: $tls
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: [ReadWriteOnce]
      resources:
        requests:
          storage: 2Gi
YAML
kubectl -n "$ns" rollout status "statefulset/$prefix" --timeout=1200s
printf 'deployed secure native Elasticsearch cluster\n' > submit.txt
