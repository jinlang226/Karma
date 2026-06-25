#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
bundle="${BENCH_PARAM_TRANSPORT_BUNDLE_CONFIGMAP:-es-transport-ca-bundle}"
ca1="${BENCH_PARAM_CA1_SECRET_NAME:-es-transport-ca1}"
ca2="${BENCH_PARAM_CA2_SECRET_NAME:-es-transport-ca2}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

kubectl -n "$ns" get secret "$ca1" -o jsonpath='{.data.ca\.crt}' | base64 -d > "$tmp/ca1.crt"
kubectl -n "$ns" get secret "$ca2" -o jsonpath='{.data.ca\.crt}' | base64 -d > "$tmp/ca2.crt"
cat "$tmp/ca1.crt" "$tmp/ca2.crt" > "$tmp/ca.crt"
kubectl -n "$ns" create configmap "$bundle" --from-file=ca.crt="$tmp/ca.crt" \
  --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
for ordinal in 0 1 2; do
  kubectl -n "$ns" delete "pod/$prefix-$ordinal" --wait=true
  for attempt in $(seq 1 60); do
    kubectl -n "$ns" get "pod/$prefix-$ordinal" >/dev/null 2>&1 && break
    sleep 2
  done
  kubectl -n "$ns" wait --for=condition=ready "pod/$prefix-$ordinal" --timeout=900s
done
printf 'expanded transport trust bundle\n' > submit.txt
