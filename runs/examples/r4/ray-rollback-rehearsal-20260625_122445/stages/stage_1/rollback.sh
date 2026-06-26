#!/bin/sh
# Rollback script: revert Ray cluster to pre-change-window baseline.
#
# Non-default configuration observed:
#   - ray-head  image: rayproject/ray:2.9.0  (upgraded from baseline 2.8.0)
#   - ray-worker image: rayproject/ray:2.9.0  (upgraded from baseline 2.8.0)
#   - ray-worker replicas: 1 (baseline; reaffirm in case scale-up occurred)
#   - ray-head Service: gcs:6379 (baseline; reaffirm in case dashboard port added)
#
# WARNING: Do NOT execute until the change window is open.
# This script is stored as a ConfigMap for review only.

set -e

NAMESPACE=ray
BASELINE_IMAGE=rayproject/ray:2.8.0
BASELINE_WORKER_REPLICAS=1

echo "=== Ray cluster rollback: reverting to baseline ==="

# 1. Revert images from 2.9.0 to baseline 2.8.0
echo "Reverting ray-head image to ${BASELINE_IMAGE} ..."
kubectl -n "${NAMESPACE}" set image deployment/ray-head \
  ray-head="${BASELINE_IMAGE}"

echo "Reverting ray-worker image to ${BASELINE_IMAGE} ..."
kubectl -n "${NAMESPACE}" set image deployment/ray-worker \
  ray-worker="${BASELINE_IMAGE}"

# 2. Revert worker replica count to baseline
echo "Reverting ray-worker replicas to ${BASELINE_WORKER_REPLICAS} ..."
kubectl -n "${NAMESPACE}" scale deployment/ray-worker \
  --replicas="${BASELINE_WORKER_REPLICAS}"

# 3. Revert ray-head Service to baseline: gcs port 6379 only.
#    Removes dashboard port (8265) if it was exposed.
echo "Reverting ray-head Service to baseline (gcs:6379 only) ..."
kubectl -n "${NAMESPACE}" patch svc ray-head --type=json \
  -p='[{"op":"replace","path":"/spec/ports","value":[{"name":"gcs","port":6379,"targetPort":6379,"protocol":"TCP"}]}]'

# 4. Remove resource limits/requests if any were set (baseline has no limits)
echo "Clearing resource limits on ray-head ..."
kubectl -n "${NAMESPACE}" patch deployment/ray-head --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/resources","value":{}}]'

echo "Clearing resource limits on ray-worker ..."
kubectl -n "${NAMESPACE}" patch deployment/ray-worker --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/resources","value":{}}]'

# 5. Wait for rollouts to stabilise
echo "Waiting for ray-head rollout ..."
kubectl -n "${NAMESPACE}" rollout status deployment/ray-head --timeout=300s

echo "Waiting for ray-worker rollout ..."
kubectl -n "${NAMESPACE}" rollout status deployment/ray-worker --timeout=300s

echo "=== Rollback complete. Post-rollback state: ==="
kubectl -n "${NAMESPACE}" get deploy ray-head ray-worker -o wide
kubectl -n "${NAMESPACE}" get svc ray-head
