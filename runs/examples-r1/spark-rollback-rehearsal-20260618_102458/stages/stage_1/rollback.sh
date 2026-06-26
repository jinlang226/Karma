#!/usr/bin/env bash
#
# rollback.sh — Spark (spark-pi) rollback rehearsal script (FOR REVIEW ONLY).
#
# Purpose:
#   Revert the spark-pi namespace back to its ORIGINAL DEFAULT configuration if
#   the non-default settings (Deployment replicas, container images, resource
#   requests/limits, and RBAC role) ever need to be undone in a change window.
#
# Original defaults (source of truth: cases/spark/deploy_spark_pi/resource/*):
#   - spark-master Deployment: replicas=1, image apache/spark:3.5.3,
#       requests cpu=200m mem=512Mi, limits cpu=500m mem=1Gi
#   - spark-worker Deployment: replicas=1, image apache/spark:3.5.3,
#       env SPARK_WORKER_MEMORY=1G SPARK_WORKER_CORES=1,
#       requests cpu=200m mem=512Mi, limits cpu=500m mem=1Gi
#   - ServiceAccount spark-pi
#   - Role spark-pi-role: ["services","configmaps","persistentvolumeclaims"]
#       verbs ["create","get","list","watch","delete","update","patch"]
#   - RoleBinding spark-pi-role-binding -> Role/spark-pi-role, subject SA spark-pi
#
# DO NOT run this during the current window — it will undo configuration the
# cluster currently depends on. Review first, then execute only inside an
# approved change window.

set -euo pipefail

NS=spark-pi
IMAGE=apache/spark:3.5.3

echo ">> Spark rollback to original defaults (namespace: ${NS})"

# --- 1. RBAC: ServiceAccount, Role, RoleBinding ----------------------------
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: spark-pi
  namespace: spark-pi
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: spark-pi-role
  namespace: spark-pi
rules:
- apiGroups: [""]
  resources: ["services", "configmaps", "persistentvolumeclaims"]
  verbs: ["create", "get", "list", "watch", "delete", "update", "patch"]
---
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

# --- 2. Deployments: replicas, image, resources ----------------------------
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
---
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
          value: "1G"
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

# --- 3. Wait for rollout (optional verification) ---------------------------
kubectl -n "${NS}" rollout status deployment/spark-master --timeout=180s
kubectl -n "${NS}" rollout status deployment/spark-worker --timeout=180s

# --- 4. Report -------------------------------------------------------------
echo ">> Rollback complete. Current state:"
kubectl -n "${NS}" get deploy spark-master spark-worker -o wide
kubectl -n "${NS}" get role,rolebinding,serviceaccount

echo ">> Done."
