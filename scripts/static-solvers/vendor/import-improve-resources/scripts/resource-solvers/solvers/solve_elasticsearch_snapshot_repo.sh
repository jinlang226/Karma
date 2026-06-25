#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
repo="${BENCH_PARAM_SNAPSHOT_REPO_NAME:-minio-repo}"
snapshot="${repo}-smoke-snapshot"
access=$(kubectl -n "$ns" get secret es-secure-settings -o jsonpath='{.data.s3\.client\.default\.access_key}' | base64 -d)
secret=$(kubectl -n "$ns" get secret es-secure-settings -o jsonpath='{.data.s3\.client\.default\.secret_key}' | base64 -d)
for pod in $(kubectl -n "$ns" get pods -l "app=${cluster}" -o jsonpath='{.items[*].metadata.name}'); do
  printf '%s' "$access" | kubectl -n "$ns" exec -i "$pod" -- \
    /usr/share/elasticsearch/bin/elasticsearch-keystore add -x -f s3.client.default.access_key
  printf '%s' "$secret" | kubectl -n "$ns" exec -i "$pod" -- \
    /usr/share/elasticsearch/bin/elasticsearch-keystore add -x -f s3.client.default.secret_key
done
kubectl -n "$ns" exec curl-test -- curl -sS -f -X POST \
  "http://${service}:9200/_nodes/reload_secure_settings" \
  -H 'Content-Type: application/json' -d '{}' >/dev/null
kubectl -n "$ns" exec curl-test -- curl -sS -f -X PUT \
  "http://${service}:9200/_snapshot/${repo}" \
  -H 'Content-Type: application/json' \
  -d '{"type":"s3","settings":{"bucket":"es-backups","client":"default"}}' >/dev/null
kubectl -n "$ns" exec curl-test -- curl -sS -f -X PUT \
  "http://${service}:9200/_snapshot/${repo}/${snapshot}?wait_for_completion=true" >/dev/null
printf 'configured Elasticsearch snapshot repository\n' > submit.txt
