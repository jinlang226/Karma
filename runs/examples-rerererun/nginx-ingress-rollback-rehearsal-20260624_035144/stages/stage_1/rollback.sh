#!/usr/bin/env bash
# rollback.sh — Revert ingress-nginx non-default configuration to vanilla defaults.
#
# What was found non-default (inspected 2026-06-24):
#   1. Deployment args: --watch-ingress-without-class=false was appended after
#      --watch-ingress-without-class=true, overriding it (live spec = generation 2,
#      two replicasets present). Rollback: restore the original arg list from the
#      last-applied-configuration annotation (removes the extra =false flag).
#   2. ConfigMap ingress-nginx-controller: no data keys found — already vanilla.
#      The patch below is a defensive no-op that ensures a clean state regardless.
#   3. Ingress resources in demo namespace: none present at prep time. The loop
#      below removes any that exist at execution time.
#
# IMPORTANT: Do NOT run this until an approved change window.
# Execution will trigger a rolling restart of the ingress-nginx controller.

set -euo pipefail

echo "=== ingress-nginx rollback: restoring vanilla defaults ==="
echo ""

# ── 1. Restore Deployment args ───────────────────────────────────────────────
# The extra --watch-ingress-without-class=false was appended as a post-install
# patch (deployment went to generation 2). The original install args
# (last-applied-configuration) had --watch-ingress-without-class=true.
# Replace the entire args list to remove the stray =false entry.

echo "[1/3] Restoring ingress-nginx-controller Deployment args ..."

kubectl -n ingress-nginx patch deployment ingress-nginx-controller \
  --type=json \
  -p='[
    {
      "op": "replace",
      "path": "/spec/template/spec/containers/0/args",
      "value": [
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
      ]
    }
  ]'

echo "  OK: extra --watch-ingress-without-class=false removed from Deployment args"

# ── 2. Clear ingress-nginx-controller ConfigMap data ─────────────────────────
# Vanilla default is an empty ConfigMap (no data keys). Remove /data if it
# exists; if the path is already absent kubectl returns an error we suppress.

echo "[2/3] Clearing ingress-nginx-controller ConfigMap data ..."

if kubectl -n ingress-nginx get configmap ingress-nginx-controller \
     -o jsonpath='{.data}' 2>/dev/null | grep -q .; then
  kubectl -n ingress-nginx patch configmap ingress-nginx-controller \
    --type=json \
    -p='[{"op":"remove","path":"/data"}]'
  echo "  OK: ConfigMap data removed"
else
  echo "  OK: ConfigMap already empty (no-op)"
fi

# ── 3. Remove Ingress resources from demo namespace ───────────────────────────
# No Ingress resources were found at prep time. This step removes any that were
# created between rollback preparation and execution.

echo "[3/3] Removing Ingress resources in demo namespace ..."

INGRESSES=$(kubectl -n demo get ingress -o name 2>/dev/null || true)
if [ -z "$INGRESSES" ]; then
  echo "  OK: no Ingress resources found (no-op)"
else
  for ing in $INGRESSES; do
    kubectl -n demo delete "$ing"
    echo "  Deleted $ing"
  done
fi

echo ""
echo "=== Rollback complete ==="
echo ""
echo "Wait for controller rollout:"
echo "  kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller"
echo ""
echo "Verify restored args (should have no '=false' entry):"
echo "  kubectl -n ingress-nginx get deploy ingress-nginx-controller \\"
echo "    -o jsonpath='{range .spec.template.spec.containers[0].args[*]}{@}{\"\n\"}{end}'"
