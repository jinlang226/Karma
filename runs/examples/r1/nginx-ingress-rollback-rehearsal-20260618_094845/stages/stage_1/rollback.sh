#!/usr/bin/env bash
#
# rollback.sh — revert non-default ingress-nginx configuration to vanilla defaults
#
# REVIEW-ONLY: this script is stored in the `rollback-rehearsal` ConfigMap for
# the team to review before the next change window. It is NOT meant to be run
# against the live cluster as part of this rehearsal.
#
# Scope of the rollback (derived from inspecting the live cluster on 2026-06-18):
#
#   ingress-nginx-controller Deployment (ns: ingress-nginx)
#     Non-default arg appended to the controller container:
#         --watch-ingress-without-class=false
#     The vanilla baseline (kubectl last-applied-configuration) carries
#         --watch-ingress-without-class=true
#     as the final flag before --publish-status-address=localhost and does NOT
#     carry the trailing `=false` override. Rollback = restore the baseline
#     arg list so the controller again watches class-less Ingresses.
#
#   ingress-nginx-controller ConfigMap (ns: ingress-nginx)
#     `data` is already null/empty (vanilla). Rollback re-asserts the empty
#     ConfigMap so any reviewed-away custom tuning keys are cleared.
#
#   Ingress resources (ns: demo)
#     None present at inspection time. Annotations are per-Ingress, so there is
#     nothing to strip; this section is a placeholder for when class-less /
#     annotated Ingresses exist at rollback time.
#
# The script is idempotent and uses `kubectl apply`/strategic patches so it can
# be re-run safely. It restarts the controller only after a real arg change.

set -euo pipefail

NS_INGRESS="ingress-nginx"
DEPLOY="ingress-nginx-controller"
CM="ingress-nginx-controller"

echo ">> Restoring ${DEPLOY} container args to the vanilla baseline set"

# The vanilla baseline arg list for the controller container. This is the exact
# set from the deployment's last-applied-configuration, i.e. WITHOUT the
# non-default `--watch-ingress-without-class=false` override.
BASELINE_ARGS='[
  "/nginx-ingress-controller",
  "--election-id=ingress-nginx-leader",
  "--controller-class=k8s.io/ingress-nginx",
  "--ingress-class=nginx",
  "--configmap=$(POD_NAMESPACE)/ingress-nginx-controller",
  "--validating-webhook=:8443",
  "--validating-webhook-certificate=/usr/local/certificates/cert",
  "--validating-webhook-key=/usr/local/certificates/key",
  "--watch-ingress-without-class=true",
  "--publish-status-address=localhost"
]'

# Replace the controller container's args wholesale with the baseline set. A
# JSON-patch "replace" on the container args removes any appended overrides
# (such as the trailing --watch-ingress-without-class=false) in one operation.
kubectl -n "${NS_INGRESS}" patch deployment "${DEPLOY}" --type=json -p "$(cat <<EOF
[
  {
    "op": "replace",
    "path": "/spec/template/spec/containers/0/args",
    "value": ${BASELINE_ARGS}
  }
]
EOF
)"

echo ">> Re-asserting an empty (vanilla) ${CM} ConfigMap"

# Clear any custom controller tuning keys back to the vanilla empty ConfigMap.
kubectl -n "${NS_INGRESS}" apply -f - <<'EOF'
apiVersion: v1
kind: ConfigMap
metadata:
  name: ingress-nginx-controller
  namespace: ingress-nginx
  labels:
    app.kubernetes.io/component: controller
    app.kubernetes.io/instance: ingress-nginx
    app.kubernetes.io/name: ingress-nginx
    app.kubernetes.io/part-of: ingress-nginx
    app.kubernetes.io/version: 1.14.1
data: {}
EOF

echo ">> Waiting for the controller to roll out with the restored configuration"
kubectl -n "${NS_INGRESS}" rollout status deployment "${DEPLOY}" --timeout=180s

echo ">> Rollback complete: ingress-nginx configuration restored to vanilla defaults"
