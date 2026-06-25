#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
aggregate="${BENCH_PARAM_AGGREGATE_SECRET_NAME:-es-file-realm-aggregate}"
provided="${BENCH_PARAM_PROVIDED_SECRET_NAME:-user-provided-file-realm}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

for field in users users_roles roles.yml; do
  json_field=$(printf '%s' "$field" | sed 's/\./\\./g')
  kubectl -n "$ns" get secret "$aggregate" -o "jsonpath={.data.${json_field}}" | base64 -d > "$tmp/aggregate-$field"
  kubectl -n "$ns" get secret "$provided" -o "jsonpath={.data.${json_field}}" | base64 -d > "$tmp/provided-$field"
  {
    cat "$tmp/aggregate-$field"
    printf '\n'
    cat "$tmp/provided-$field"
  } > "$tmp/$field"
done
kubectl -n "$ns" create secret generic "$aggregate" \
  --from-file=users="$tmp/users" \
  --from-file=users_roles="$tmp/users_roles" \
  --from-file=roles.yml="$tmp/roles.yml" \
  --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
kubectl -n "$ns" rollout restart "statefulset/$prefix"
kubectl -n "$ns" rollout status "statefulset/$prefix" --timeout=900s
printf 'merged file realm users and roles\n' > submit.txt
