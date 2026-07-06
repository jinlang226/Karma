#!/usr/bin/env bash
#
# rollback-rehearsal: revert the ray namespace to its original baseline.
#
# Baseline values were captured from the deployments'
# kubectl.kubernetes.io/last-applied-configuration and the ray-head Service:
#   - ray-head   replicas: 1
#   - ray-worker replicas: 1
#   - image (both):        rayproject/ray:2.9.0
#   - container resources: {} (no requests/limits)
#   - ray-head Service:    single port  gcs / 6379 -> 6379 (no dashboard port)
#
# Review before running. This script is idempotent: applying it when the
# cluster already matches baseline is a no-op.
set -euo pipefail

NS=ray

echo "[rollback] scaling deployments back to baseline replica counts"
kubectl -n "$NS" scale deploy/ray-head   --replicas=1
kubectl -n "$NS" scale deploy/ray-worker --replicas=1

echo "[rollback] reverting container images to rayproject/ray:2.9.0"
kubectl -n "$NS" set image deploy/ray-head   ray-head=rayproject/ray:2.9.0
kubectl -n "$NS" set image deploy/ray-worker ray-worker=rayproject/ray:2.9.0

echo "[rollback] clearing container resource requests/limits (baseline: none)"
kubectl -n "$NS" patch deploy/ray-head --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/resources","value":{}}]'
kubectl -n "$NS" patch deploy/ray-worker --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/resources","value":{}}]'

echo "[rollback] restoring ray-head Service to the single baseline gcs port"
kubectl -n "$NS" patch svc/ray-head --type=merge -p='{
  "spec": {
    "ports": [
      {"name": "gcs", "port": 6379, "targetPort": 6379, "protocol": "TCP"}
    ]
  }
}'

echo "[rollback] waiting for rollouts to settle"
kubectl -n "$NS" rollout status deploy/ray-head
kubectl -n "$NS" rollout status deploy/ray-worker

echo "[rollback] complete — ray namespace reverted to baseline"
