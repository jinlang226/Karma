#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/initialize
# Strategy: native_shell
# Notes: Repairs the join configuration and initializes the cluster without
# changing the inherited CockroachDB image version.

static_solver_export_namespace_if_unset "cockroachdb"
static_solver_export_cockroachdb_defaults

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX}"
replicas="${BENCH_PARAM_REPLICA_COUNT}"
label_selector="app.kubernetes.io/instance=${prefix}"
current_image="$(
  kubectl -n "${ns}" get statefulset "${prefix}" \
    -o jsonpath='{.spec.template.spec.containers[0].image}'
)"
join_hosts=""

[[ "${replicas}" =~ ^[0-9]+$ ]] || static_solver_fail "replica count must be numeric"
[[ -n "${current_image}" ]] || static_solver_fail "failed to resolve current CockroachDB image"

for ordinal in $(seq 0 $((replicas - 1))); do
  host="${prefix}-${ordinal}.${prefix}.${ns}.svc.cluster.local:26257"
  if [[ -n "${join_hosts}" ]]; then
    join_hosts="${join_hosts},${host}"
  else
    join_hosts="${host}"
  fi
done

cat <<EOF | kubectl -n "${ns}" apply -f -
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: ${prefix}
  labels:
    app.kubernetes.io/name: cockroachdb
    app.kubernetes.io/instance: ${prefix}
spec:
  serviceName: ${prefix}
  podManagementPolicy: Parallel
  replicas: ${replicas}
  selector:
    matchLabels:
      app.kubernetes.io/name: cockroachdb
      app.kubernetes.io/instance: ${prefix}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: cockroachdb
        app.kubernetes.io/instance: ${prefix}
    spec:
      serviceAccountName: ${prefix}-sa
      containers:
      - name: db
        image: ${current_image}
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: 26257
          name: grpc
        - containerPort: 8080
          name: http
        volumeMounts:
        - name: datadir
          mountPath: /cockroach/cockroach-data
        env:
        - name: POD_NAME
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: POD_NAMESPACE
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
        command:
        - /bin/bash
        - -c
        - >-
          exec /cockroach/cockroach start
          --logtostderr=INFO
          --insecure
          --advertise-host=\$(POD_NAME).${prefix}.\$(POD_NAMESPACE).svc.cluster.local
          --http-addr=0.0.0.0:8080
          --port=26257
          --cache=25%
          --max-sql-memory=25%
          --join=${join_hosts}
        resources:
          requests:
            cpu: "1"
            memory: "2Gi"
          limits:
            cpu: "1"
            memory: "2Gi"
        livenessProbe:
          httpGet:
            path: /health
            port: http
          initialDelaySeconds: 30
          periodSeconds: 5
        readinessProbe:
          httpGet:
            path: /health?ready=1
            port: http
          initialDelaySeconds: 10
          periodSeconds: 5
          failureThreshold: 2
      terminationGracePeriodSeconds: 10
  volumeClaimTemplates:
  - metadata:
      name: datadir
      labels:
        app.kubernetes.io/name: cockroachdb
        app.kubernetes.io/instance: ${prefix}
    spec:
      accessModes:
      - ReadWriteOnce
      resources:
        requests:
          storage: 10Gi
      volumeMode: Filesystem
EOF

kubectl -n "${ns}" delete pod -l "${label_selector}" --wait=false

for ordinal in $(seq 0 $((replicas - 1))); do
  pod_name="${prefix}-${ordinal}"
  kubectl -n "${ns}" wait --for=jsonpath='{.status.phase}'=Running "pod/${pod_name}" --timeout=300s
done

initialized=false
for _ in $(seq 1 60); do
  if out="$(
    kubectl -n "${ns}" exec "${prefix}-0" -- \
      ./cockroach init --insecure \
      --host="${prefix}-0.${prefix}.${ns}.svc.cluster.local" 2>&1
  )"; then
    initialized=true
    break
  fi
  if grep -qi 'already been initialized' <<<"${out}"; then
    initialized=true
    break
  fi
  sleep 2
done
[[ "${initialized}" == "true" ]] || static_solver_fail "failed to initialize CockroachDB cluster"

kubectl -n "${ns}" wait --for=condition=ready pod -l "${label_selector}" --timeout=900s

static_solver_write_submit "initialized CockroachDB cluster"
