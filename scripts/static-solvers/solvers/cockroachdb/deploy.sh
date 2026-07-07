#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/deploy
# Strategy: native_shell
# Notes: Applies a deploy spec whose StatefulSet command contains the explicit
# node join list required by the current oracle, then initializes the cluster.

static_solver_export_namespace_if_unset "cockroachdb"
static_solver_export_cockroachdb_defaults

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX}"
replicas="${BENCH_PARAM_REPLICA_COUNT}"
storage_size_gi="${BENCH_PARAM_STORAGE_SIZE_GI:-10}"
to_version="${BENCH_PARAM_TO_VERSION:-24.1.0}"
min_available=$((replicas - 1))
label_selector="app.kubernetes.io/instance=${prefix}"
advertise_host="\$(POD_NAME).${prefix}.\$(POD_NAMESPACE).svc.cluster.local"
join_hosts=""

wait_for_pod_exists() {
  local pod_name="${1:?pod name is required}"
  local timeout_sec="${2:-120}"
  local deadline=$((SECONDS + timeout_sec))
  while (( SECONDS < deadline )); do
    if kubectl -n "${ns}" get pod "${pod_name}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  static_solver_fail "pod/${pod_name} was not created within ${timeout_sec}s"
}

for ordinal in $(seq 0 $((replicas - 1))); do
  host="${prefix}-${ordinal}.${prefix}.\$(POD_NAMESPACE).svc.cluster.local:26257"
  if [[ -n "${join_hosts}" ]]; then
    join_hosts="${join_hosts},${host}"
  else
    join_hosts="${host}"
  fi
done

cat <<EOF | kubectl -n "${ns}" apply -f -
apiVersion: v1
kind: Service
metadata:
  name: ${prefix}
  labels:
    app.kubernetes.io/name: cockroachdb
    app.kubernetes.io/instance: ${prefix}
    app.kubernetes.io/component: database
spec:
  clusterIP: None
  publishNotReadyAddresses: true
  ports:
  - port: 26257
    targetPort: 26257
    name: grpc
  - port: 8080
    targetPort: 8080
    name: http
  selector:
    app.kubernetes.io/name: cockroachdb
    app.kubernetes.io/instance: ${prefix}
---
apiVersion: v1
kind: Service
metadata:
  name: ${prefix}-public
  labels:
    app.kubernetes.io/name: cockroachdb
    app.kubernetes.io/instance: ${prefix}
    app.kubernetes.io/component: database
spec:
  type: ClusterIP
  ports:
  - port: 26257
    targetPort: 26257
    name: grpc
  - port: 8080
    targetPort: 8080
    name: http
  selector:
    app.kubernetes.io/name: cockroachdb
    app.kubernetes.io/instance: ${prefix}
---
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
        image: cockroachdb/cockroach:v${to_version}
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
          --advertise-host=${advertise_host}
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
      terminationGracePeriodSeconds: 60
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
          storage: ${storage_size_gi}Gi
      volumeMode: Filesystem
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: ${prefix}-pdb
  labels:
    app.kubernetes.io/name: cockroachdb
    app.kubernetes.io/instance: ${prefix}
spec:
  minAvailable: ${min_available}
  selector:
    matchLabels:
      app.kubernetes.io/name: cockroachdb
      app.kubernetes.io/instance: ${prefix}
EOF

wait_for_pod_exists "${prefix}-0"
kubectl -n "${ns}" wait --for=jsonpath='{.status.phase}'=Running "pod/${prefix}-0" --timeout=300s

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

for _ in $(seq 1 60); do
  pod_count="$(
    kubectl -n "${ns}" get pods -l "${label_selector}" --no-headers 2>/dev/null | wc -l | tr -d ' '
  )"
  if [[ "${pod_count}" == "${replicas}" ]]; then
    break
  fi
  sleep 2
done

kubectl -n "${ns}" wait --for=condition=ready pod -l "${label_selector}" --timeout=900s
[[ "$(kubectl -n "${ns}" get sts "${prefix}" -o jsonpath='{.status.readyReplicas}')" == "${replicas}" ]]

static_solver_write_submit "deployed CockroachDB cluster"
