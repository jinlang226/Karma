#!/usr/bin/env bash
# rollback.sh — revert ingress-nginx to vanilla defaults
#
# Reverts all non-default configuration applied on top of the stock
# ingress-nginx v1.14.1 install:
#   1. ConfigMap ingress-nginx-controller  → clear all custom data keys
#   2. Extra ConfigMaps                    → delete (added post-install)
#   3. Deployment ingress-nginx-controller → remove OTel env var,
#                                           remove duplicate watch-ingress arg,
#                                           remove restartedAt annotation
#   4. Ingress annotations in demo ns      → remove rewrite-target, canary,
#                                           and rate-limit annotations
#
# Run with: bash rollback.sh
# Requires: kubectl pointed at the target cluster with sufficient RBAC.
#
# DO NOT run this against a live cluster unless the change window is open.
# Review each section carefully before executing.

set -euo pipefail

echo "==> [1/4] Reset ingress-nginx-controller ConfigMap to empty (vanilla default)"
# The original install applied data: null — all keys below are non-default additions.
# Keys being removed:
#   enable-opentelemetry, limit-req-status-code, log-format-upstream (otel variant),
#   main-snippet, opentelemetry-collector-host, opentelemetry-collector-port,
#   otel-sampler, otel-sampler-parent-based, otel-sampler-ratio
kubectl -n ingress-nginx patch configmap ingress-nginx-controller \
  --type=merge \
  -p '{"data": null}'

echo "==> [2/4] Delete non-standard ConfigMaps added post-install"
# ingress-nginx-controller-extra: a decoy ConfigMap (benchmark.decoy=true) that
#   was never part of the stock install; log-format-upstream key only.
# ingress-nginx-otel-config: OTel TOML config mounted by otel-enabled controller.
#   Neither existed before the non-default configuration was applied.
kubectl -n ingress-nginx delete configmap \
  ingress-nginx-controller-extra \
  ingress-nginx-otel-config \
  --ignore-not-found

echo "==> [3/4] Restore ingress-nginx-controller Deployment to original spec"
# Three deviations from the original last-applied-configuration are patched back:
#
# a) Extra arg --watch-ingress-without-class=false was appended to override the
#    original --watch-ingress-without-class=true; the original list is restored.
#
# b) OTEL_EXPORTER_OTLP_ENDPOINT env var was injected to configure OTel export;
#    removed so the env matches the stock install (POD_NAME, POD_NAMESPACE,
#    LD_PRELOAD only).
#
# c) kubectl.kubernetes.io/restartedAt annotation was added to the pod template by
#    a `kubectl rollout restart`; removed so template metadata matches original.
#
# Strategic merge patch uses container name "controller" as the merge key, so only
# the args and env arrays are replaced — all other container fields are untouched.
kubectl -n ingress-nginx patch deployment ingress-nginx-controller \
  --type=strategic \
  -p "$(cat <<'PATCH'
spec:
  template:
    metadata:
      annotations:
        kubectl.kubernetes.io/restartedAt: null
    spec:
      containers:
      - name: controller
        args:
        - /nginx-ingress-controller
        - --election-id=ingress-nginx-leader
        - --controller-class=k8s.io/ingress-nginx
        - --ingress-class=nginx
        - --configmap=$(POD_NAMESPACE)/ingress-nginx-controller
        - --validating-webhook=:8443
        - --validating-webhook-certificate=/usr/local/certificates/cert
        - --validating-webhook-key=/usr/local/certificates/key
        - --watch-ingress-without-class=true
        - --publish-status-address=localhost
        env:
        - name: POD_NAME
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: POD_NAMESPACE
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
        - name: LD_PRELOAD
          value: /usr/local/lib/libmimalloc.so
PATCH
)"

echo "==> [4/4] Remove non-default nginx-ingress annotations from Ingress resources in demo ns"

# demo-app: nginx.ingress.kubernetes.io/rewrite-target=/ was added;
#   vanilla Ingress has no rewrite rules.
kubectl -n demo annotate ingress demo-app \
  nginx.ingress.kubernetes.io/rewrite-target- \
  --overwrite 2>/dev/null || true

# canary-canary: all three canary annotations added to enable header-based routing.
kubectl -n demo annotate ingress canary-canary \
  nginx.ingress.kubernetes.io/canary- \
  nginx.ingress.kubernetes.io/canary-by-header- \
  nginx.ingress.kubernetes.io/canary-by-header-value- \
  --overwrite 2>/dev/null || true

# canary-alt: same canary annotation set (decoy variant, benchmark.decoy=true).
kubectl -n demo annotate ingress canary-alt \
  nginx.ingress.kubernetes.io/canary- \
  nginx.ingress.kubernetes.io/canary-by-header- \
  nginx.ingress.kubernetes.io/canary-by-header-value- \
  --overwrite 2>/dev/null || true

# rate-api: nginx.ingress.kubernetes.io/limit-rps=2 was applied to enforce
#   per-source rate limiting; removing restores unrestricted throughput.
kubectl -n demo annotate ingress rate-api \
  nginx.ingress.kubernetes.io/limit-rps- \
  --overwrite 2>/dev/null || true

echo ""
echo "Rollback complete. Verify with:"
echo "  kubectl -n ingress-nginx get configmap ingress-nginx-controller -o yaml"
echo "  kubectl -n ingress-nginx get deploy ingress-nginx-controller -o jsonpath='{.spec.template.spec.containers[0].args}'"
echo "  kubectl -n demo get ingress -o jsonpath='{range .items[*]}{.metadata.name}: {.metadata.annotations}{\"\\n\"}{end}'"
