#!/usr/bin/env bash
# Rollback script: revert ingress-nginx non-default configuration to vanilla defaults.
#
# Changes this script undoes (identified 2026-06-26):
#   1. Removes the extra --watch-ingress-without-class=false arg that was appended to
#      the controller deployment via kubectl patch, overriding the original =true flag.
#      Restores the args array to exactly what was in the last-applied-configuration.
#   2. Deletes IngressClass "ingress-1" and "ingress-2" which were added outside the
#      standard ingress-nginx install (controller sees them but ignores them).
#   3. Resets the ingress-nginx-controller ConfigMap data to empty (currently already
#      empty, but included for completeness in case data was staged).
#
# DO NOT run this against a cluster that depends on these settings being in place.
# Review with: kubectl -n demo get configmap rollback-rehearsal -o jsonpath='{.data.rollback\.sh}'
set -euo pipefail

echo "==> [1/3] Restoring ingress-nginx-controller deployment args to original state"
# Replace the full args array using a JSON strategic merge patch.
# Removes the trailing --watch-ingress-without-class=false that was appended post-apply.
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
echo "    Deployment patched. Waiting for rollout..."
kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout=120s

echo "==> [2/3] Deleting non-default IngressClass resources (ingress-1, ingress-2)"
# These IngressClasses were added outside the standard ingress-nginx install manifest.
# The vanilla install only creates the 'nginx' IngressClass.
for ic in ingress-1 ingress-2; do
  if kubectl get ingressclass "$ic" >/dev/null 2>&1; then
    kubectl delete ingressclass "$ic"
    echo "    Deleted IngressClass: $ic"
  else
    echo "    IngressClass $ic not found, skipping"
  fi
done

echo "==> [3/3] Resetting ingress-nginx-controller ConfigMap data to empty (vanilla)"
# The original install applied the ConfigMap with data: null.
# This patch ensures no leftover custom keys remain.
kubectl -n ingress-nginx patch configmap ingress-nginx-controller \
  --type=merge \
  -p='{"data": null}'
echo "    ConfigMap reset to empty data."

echo ""
echo "Rollback complete. Verify with:"
echo "  kubectl -n ingress-nginx get deploy ingress-nginx-controller -o jsonpath='{.spec.template.spec.containers[0].args}'"
echo "  kubectl get ingressclass"
echo "  kubectl -n ingress-nginx get configmap ingress-nginx-controller -o yaml"
