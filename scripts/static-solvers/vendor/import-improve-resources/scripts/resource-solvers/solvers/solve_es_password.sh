#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
current="${BENCH_PARAM_CURRENT_PASSWORD_SECRET_NAME:-elastic-password}"
next="${BENCH_PARAM_NEXT_PASSWORD_SECRET_NAME:-elastic-password-next}"
checker="${BENCH_PARAM_AUTH_CHECKER_DEPLOYMENT_NAME:-auth-checker}"

old=$(kubectl -n "$ns" get secret "$current" -o jsonpath='{.data.password}' | base64 -d)
new=$(kubectl -n "$ns" get secret "$next" -o jsonpath='{.data.password}' | base64 -d)
kubectl -n "$ns" exec curl-test -- curl -fsS -u "elastic:$old" \
  -XPOST "http://$service:9200/_security/user/elastic/_password" \
  -H 'Content-Type: application/json' -d "{\"password\":\"$new\"}" >/dev/null
kubectl -n "$ns" create secret generic "$current" --from-literal=password="$new" \
  --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
kubectl -n "$ns" rollout restart "deployment/$checker"
kubectl -n "$ns" rollout status "deployment/$checker" --timeout=600s
printf 'rotated elastic password and dependent secret\n' > submit.txt
