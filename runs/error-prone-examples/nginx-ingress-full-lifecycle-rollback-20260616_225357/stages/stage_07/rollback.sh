#!/usr/bin/env bash
# rollback.sh — Revert non-default ingress-nginx configuration to vanilla defaults.
#
# REVIEW ONLY — do NOT execute against the live cluster until the change window.
# Running this will:
#   1. Clear all custom data keys from the ingress-nginx-controller ConfigMap
#      (removes OTel pipeline, custom log-format, limit-req-status-code override).
#   2. Strip two non-default controller args from the Deployment
#      (--watch-ingress-without-class=true, --publish-status-address=localhost).
#      This triggers a rolling restart of the controller pod.
#   3. Revert annotation/spec drift on three Ingress resources in the demo namespace
#      that diverged from their last-applied state after initial deployment.
#
# Pre-run checklist:
#   [ ] kubectl context points at the correct cluster
#   [ ] No active change freeze in effect
#   [ ] Downstream services that rely on OTel traces have been notified
#   [ ] rate-api traffic is low (rate-limit change from rps=1 -> rps=2 takes effect on reload)

set -euo pipefail

INGRESS_NS="ingress-nginx"
DEMO_NS="demo"

# ---------------------------------------------------------------------------
# 1. Reset ingress-nginx-controller ConfigMap to vanilla (empty data)
# ---------------------------------------------------------------------------
# Current non-default keys and their values:
#   enable-opentelemetry:    "true"           (vanilla: absent / disabled)
#   limit-req-status-code:   "429"            (vanilla: absent / nginx default 503)
#   log-format-upstream:     <otel-enriched>  (vanilla: absent / nginx default format)
#   otel-sampler:            AlwaysOn         (vanilla: absent)
#   otel-sampler-parent-based: "false"        (vanilla: absent)
#   otel-sampler-ratio:      "1.0"            (vanilla: absent)
#   otlp-collector-host:     otel-collector.otel.svc  (vanilla: absent)
#   otlp-collector-port:     "4317"           (vanilla: absent)
echo "=== [1/5] Clearing non-default ConfigMap data keys ==="
kubectl -n "$INGRESS_NS" patch configmap ingress-nginx-controller \
  --type=json \
  -p '[
    {"op": "remove", "path": "/data/enable-opentelemetry"},
    {"op": "remove", "path": "/data/limit-req-status-code"},
    {"op": "remove", "path": "/data/log-format-upstream"},
    {"op": "remove", "path": "/data/otel-sampler"},
    {"op": "remove", "path": "/data/otel-sampler-parent-based"},
    {"op": "remove", "path": "/data/otel-sampler-ratio"},
    {"op": "remove", "path": "/data/otlp-collector-host"},
    {"op": "remove", "path": "/data/otlp-collector-port"}
  ]'
echo "ConfigMap ingress-nginx-controller data cleared."

# ---------------------------------------------------------------------------
# 2. Remove non-default controller args from the Deployment
# ---------------------------------------------------------------------------
# Non-default flags currently present:
#   --watch-ingress-without-class=true   (vanilla: absent / default false)
#   --publish-status-address=localhost   (vanilla: absent)
#
# Restores to the standard v1.14.1 arg list used by the upstream install manifest.
echo ""
echo "=== [2/5] Restoring vanilla Deployment args ==="
kubectl -n "$INGRESS_NS" patch deployment ingress-nginx-controller \
  --type=json \
  -p '[
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
        "--validating-webhook-key=/usr/local/certificates/key"
      ]
    }
  ]'
echo "Deployment args restored. Controller pod rolling restart will follow."

# ---------------------------------------------------------------------------
# 3. Revert rate-api Ingress: restore limit-rps=2 and remove limit-burst-multiplier
# ---------------------------------------------------------------------------
# Last-applied state: limit-rps="2", no limit-burst-multiplier annotation.
# Live state:         limit-rps="1", limit-burst-multiplier="1" (added via patch).
echo ""
echo "=== [3/5] Reverting rate-api Ingress annotation drift ==="
kubectl -n "$DEMO_NS" patch ingress rate-api \
  --type=json \
  -p '[
    {
      "op": "replace",
      "path": "/metadata/annotations/nginx.ingress.kubernetes.io~1limit-rps",
      "value": "2"
    },
    {
      "op": "remove",
      "path": "/metadata/annotations/nginx.ingress.kubernetes.io~1limit-burst-multiplier"
    }
  ]'
echo "rate-api: limit-rps restored to 2, limit-burst-multiplier removed."

# ---------------------------------------------------------------------------
# 4. Revert canary-canary Ingress: restore ingressClassName=nginx
# ---------------------------------------------------------------------------
# Last-applied: ingressClassName=nginx.  Live (gen 2): ingressClassName=ingress-1.
echo ""
echo "=== [4/5] Reverting canary-canary Ingress ingressClassName ==="
kubectl -n "$DEMO_NS" patch ingress canary-canary \
  --type=merge \
  -p '{"spec": {"ingressClassName": "nginx"}}'
echo "canary-canary: ingressClassName restored to nginx."

# ---------------------------------------------------------------------------
# 5. Revert canary-stable Ingress: restore ingressClassName=nginx
# ---------------------------------------------------------------------------
# Last-applied: ingressClassName=nginx.  Live (gen 2): ingressClassName=ingress-1.
echo ""
echo "=== [5/5] Reverting canary-stable Ingress ingressClassName ==="
kubectl -n "$DEMO_NS" patch ingress canary-stable \
  --type=merge \
  -p '{"spec": {"ingressClassName": "nginx"}}'
echo "canary-stable: ingressClassName restored to nginx."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "=== Rollback complete ==="
echo ""
echo "Post-rollback verification steps:"
echo "  kubectl -n ingress-nginx get configmap ingress-nginx-controller -o yaml"
echo "    → data: field should be empty (or absent)"
echo ""
echo "  kubectl -n ingress-nginx get deploy ingress-nginx-controller -o jsonpath='{.spec.template.spec.containers[0].args}'"
echo "    → should not contain --watch-ingress-without-class or --publish-status-address"
echo ""
echo "  kubectl -n demo get ingress rate-api -o jsonpath='{.metadata.annotations}'"
echo "    → limit-rps=2, no limit-burst-multiplier"
echo ""
echo "  kubectl -n demo get ingress canary-canary canary-stable -o jsonpath='{range .items[*]}{.metadata.name}: {.spec.ingressClassName}{\"\\n\"}{end}'"
echo "    → both should show nginx"
echo ""
echo "  kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller"
echo "    → wait for controller pod to complete rolling restart"
