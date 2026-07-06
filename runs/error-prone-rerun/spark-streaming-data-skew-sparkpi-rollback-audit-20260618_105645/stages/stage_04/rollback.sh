#!/usr/bin/env bash
#
# rollback.sh — Revert the spark-pi namespace to its original default
# configuration ahead of the change window.
#
# PURPOSE (review only — DO NOT run against the live cluster yet):
#   The spark-pi environment was adjusted away from its original applied
#   defaults. This script restores the original declared manifests for the
#   spark-master / spark-worker Deployments and the spark-pi RBAC objects.
#
# DRIFT DETECTED (current live state vs. original last-applied-configuration):
#   * Role/spark-pi-role: an extra resource "pods" was added to the rule
#     (original resources: services, configmaps, persistentvolumeclaims).
#     This script removes "pods", restoring the original least-privilege rule.
#   * Deployments (replicas=1, image apache/spark:3.5.3, requests
#     200m/512Mi, limits 500m/1Gi) are pinned explicitly below so the
#     rollback restores them even if they are later changed.
#
# The script re-applies the ORIGINAL manifests, which reverts any field that
# has drifted from the declared defaults.

set -euo pipefail

NS=spark-pi

echo ">> Reverting spark-pi to original default configuration..."

# --- spark-master Deployment (original defaults) ---------------------------
kubectl apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: spark-master
  namespace: spark-pi
spec:
  replicas: 1
  selector:
    matchLabels:
      app: spark-master
  template:
    metadata:
      labels:
        app: spark-master
        component: master
    spec:
      serviceAccountName: spark-pi
      containers:
      - name: spark-master
        image: apache/spark:3.5.3
        command:
        - /bin/bash
        - -c
        - |
          unset SPARK_MASTER_PORT
          /opt/spark/sbin/start-master.sh
          tail -f /opt/spark/logs/*
        ports:
        - containerPort: 7077
          name: spark
        - containerPort: 8080
          name: webui
        resources:
          requests:
            cpu: 200m
            memory: 512Mi
          limits:
            cpu: 500m
            memory: 1Gi
EOF

# --- spark-worker Deployment (original defaults) ---------------------------
kubectl apply -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: spark-worker
  namespace: spark-pi
spec:
  replicas: 1
  selector:
    matchLabels:
      app: spark-worker
  template:
    metadata:
      labels:
        app: spark-worker
        component: worker
    spec:
      serviceAccountName: spark-pi
      containers:
      - name: spark-worker
        image: apache/spark:3.5.3
        command:
        - /bin/bash
        - -c
        - |
          /opt/spark/sbin/start-worker.sh spark://spark-master:7077
          tail -f /opt/spark/logs/*
        env:
        - name: SPARK_WORKER_MEMORY
          value: 1G
        - name: SPARK_WORKER_CORES
          value: "1"
        ports:
        - containerPort: 8081
        resources:
          requests:
            cpu: 200m
            memory: 512Mi
          limits:
            cpu: 500m
            memory: 1Gi
EOF

# --- spark-pi ServiceAccount (original default) ----------------------------
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: spark-pi
  namespace: spark-pi
EOF

# --- spark-pi-role Role (original default — removes added "pods") ----------
kubectl apply -f - <<'EOF'
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: spark-pi-role
  namespace: spark-pi
rules:
- apiGroups: [""]
  resources:
  - services
  - configmaps
  - persistentvolumeclaims
  verbs:
  - create
  - get
  - list
  - watch
  - delete
  - update
  - patch
EOF

# --- spark-pi-role-binding RoleBinding (original default) ------------------
kubectl apply -f - <<'EOF'
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: spark-pi-role-binding
  namespace: spark-pi
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: spark-pi-role
subjects:
- kind: ServiceAccount
  name: spark-pi
  namespace: spark-pi
EOF

echo ">> Rollback complete. Verify with:"
echo "   kubectl get deploy,role,rolebinding,sa -n ${NS}"
