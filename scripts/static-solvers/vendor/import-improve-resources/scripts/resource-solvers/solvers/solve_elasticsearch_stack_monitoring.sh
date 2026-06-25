#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
monitoring_service="${BENCH_PARAM_MONITORING_SERVICE_NAME:-monitoring-es-http}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for beat in metricbeat filebeat; do
  cm="${cluster}-${beat}-config"
  key="${beat}.yml"
  kubectl -n "$ns" get configmap "$cm" -o "jsonpath={.data.${beat}\\.yml}" \
    | sed "s/${monitoring_service}-typo/${monitoring_service}/g" > "$tmp/$key"
  kubectl -n "$ns" create configmap "$cm" --from-file="$key=$tmp/$key" \
    --dry-run=client -o yaml | kubectl -n "$ns" apply -f -
done
kubectl -n "$ns" rollout restart "statefulset/${cluster}"
kubectl -n "$ns" rollout status "statefulset/${cluster}" --timeout=600s
for _ in $(seq 1 60); do
  indices=$(kubectl -n "$ns" exec monitoring-curl-test -- \
    curl -sS "http://${monitoring_service}:9200/_cat/indices?format=json" || true)
  printf '%s' "$indices" | grep -Eq '"index":"\.monitoring-es' && break
  sleep 5
done
printf 'restored Elasticsearch monitoring flow\n' > submit.txt
