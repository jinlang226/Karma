#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/health-check-recovery
# Strategy: native_shell
# Notes: Restores the intended replica count while preserving the inherited
# image patch level and secure/insecure cluster mode.

static_solver_export_namespace_if_unset "cockroachdb"
static_solver_export_cockroachdb_defaults

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX}"
desired_replicas="${BENCH_PARAM_REPLICA_COUNT}"
current_image="$(
  kubectl -n "${ns}" get statefulset "${prefix}" \
    -o jsonpath='{.spec.template.spec.containers[0].image}'
)"
cert_secret="${prefix}-certs"
conn_flag=(--insecure)
probe_scheme=""
join_addrs=()

[[ -n "${current_image}" ]] || static_solver_fail "failed to resolve current CockroachDB image"
[[ "${desired_replicas}" =~ ^[0-9]+$ ]] || static_solver_fail "replica count must be numeric"

for i in $(seq 0 $((desired_replicas - 1))); do
  join_addrs+=("${prefix}-${i}.${prefix}.${ns}.svc.cluster.local:26257")
done
join_csv="$(IFS=,; printf '%s' "${join_addrs[*]}")"

if kubectl -n "${ns}" exec "${prefix}-0" -- ls /cockroach/cockroach-certs/ca.crt >/dev/null 2>&1; then
  conn_flag=(--certs-dir=/cockroach/cockroach-certs)
  probe_scheme="            scheme: HTTPS"
  static_solver_log "restoring secure CockroachDB health checks for ${current_image}"
  cat <<EOF | kubectl -n "${ns}" apply -f -
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: ${prefix}
  namespace: ${ns}
  labels:
    app.kubernetes.io/name: cockroachdb
    app.kubernetes.io/instance: ${prefix}
spec:
  serviceName: ${prefix}
  podManagementPolicy: Parallel
  replicas: ${desired_replicas}
  updateStrategy:
    type: RollingUpdate
    rollingUpdate:
      partition: 0
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
        - name: certs
          mountPath: /cockroach/cockroach-certs
          readOnly: true
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
          --advertise-host=\$(POD_NAME).${prefix}.\$(POD_NAMESPACE).svc.cluster.local
          --http-addr=0.0.0.0:8080
          --port=26257
          --cache=25%
          --max-sql-memory=25%
          --certs-dir=/cockroach/cockroach-certs
          --join=${join_csv}
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
${probe_scheme}
          initialDelaySeconds: 30
          periodSeconds: 5
        readinessProbe:
          httpGet:
            path: /health?ready=1
            port: http
${probe_scheme}
          initialDelaySeconds: 10
          periodSeconds: 5
          failureThreshold: 2
      volumes:
      - name: certs
        secret:
          secretName: ${cert_secret}
          defaultMode: 256
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
          storage: 10Gi
      volumeMode: Filesystem
EOF
else
  static_solver_log "restoring insecure CockroachDB health checks for ${current_image}"
  cat <<EOF | kubectl -n "${ns}" apply -f -
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: ${prefix}
  namespace: ${ns}
  labels:
    app.kubernetes.io/name: cockroachdb
    app.kubernetes.io/instance: ${prefix}
spec:
  serviceName: ${prefix}
  podManagementPolicy: Parallel
  replicas: ${desired_replicas}
  updateStrategy:
    type: RollingUpdate
    rollingUpdate:
      partition: 0
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
          --join=${join_csv}
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
          storage: 10Gi
      volumeMode: Filesystem
EOF
fi

kubectl -n "${ns}" rollout status "statefulset/${prefix}" --timeout=900s

for _ in $(seq 1 120); do
  pod_status="$(
    kubectl -n "${ns}" get pods -l app.kubernetes.io/name=cockroachdb \
      -o jsonpath='{range .items[*]}{.metadata.name} {.status.phase} {.status.containerStatuses[0].ready}{"\n"}{end}' \
      2>/dev/null || true
  )"
  pod_count="$(printf '%s\n' "${pod_status}" | sed '/^$/d' | wc -l | tr -d ' ')"
  ready_count="$(printf '%s\n' "${pod_status}" | awk '$2 == "Running" && $3 == "true" {count++} END {print count+0}')"
  if [[ "${pod_count}" = "${desired_replicas}" && "${ready_count}" = "${desired_replicas}" ]]; then
    break
  fi
  sleep 5
done

pod_status="$(
  kubectl -n "${ns}" get pods -l app.kubernetes.io/name=cockroachdb \
    -o jsonpath='{range .items[*]}{.metadata.name} {.status.phase} {.status.containerStatuses[0].ready}{"\n"}{end}' \
    2>/dev/null || true
)"
pod_count="$(printf '%s\n' "${pod_status}" | sed '/^$/d' | wc -l | tr -d ' ')"
ready_count="$(printf '%s\n' "${pod_status}" | awk '$2 == "Running" && $3 == "true" {count++} END {print count+0}')"
if [[ "${pod_count}" != "${desired_replicas}" || "${ready_count}" != "${desired_replicas}" ]]; then
  static_solver_fail "CockroachDB pods did not recover to ${desired_replicas} ready replicas"
fi

for _ in $(seq 1 120); do
  if ! kubectl -n "${ns}" exec "${prefix}-0" -- \
    ./cockroach sql "${conn_flag[@]}" -e 'SELECT 1;' >/dev/null 2>&1; then
    sleep 5
    continue
  fi

  node_status="$(
    kubectl -n "${ns}" exec "${prefix}-0" -- \
      ./cockroach node status "${conn_flag[@]}" --format=tsv 2>/dev/null || true
  )"
  live_nodes="$(printf '%s\n' "${node_status}" | awk -F'\t' '
    NR == 1 {
      for (i = 1; i <= NF; i++) {
        if ($i == "is_live") {
          col = i
        }
      }
      next
    }
    col && tolower($col) == "true" {
      count++
    }
    END {
      print count + 0
    }
  ')"
  if [[ "${live_nodes}" = "${desired_replicas}" ]]; then
    static_solver_write_submit "repaired CockroachDB health checks"
    exit 0
  fi
  sleep 5
done

static_solver_fail "CockroachDB cluster did not recover ${desired_replicas} live nodes"
