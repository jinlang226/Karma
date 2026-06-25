#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
config="${BENCH_PARAM_CONFIGMAP_NAME:-es-config}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"

kubectl -n "$ns" get configmap "$config" -o json | python3 -c '
import json, sys
namespace, prefix = sys.argv[1:3]
obj = json.load(sys.stdin)
text = obj["data"]["elasticsearch.yml"].replace("${BENCH_NAMESPACE}", namespace)
out = []
skipping = False
for line in text.splitlines():
    if line.strip() == "cluster.initial_master_nodes:":
        skipping = True
        continue
    if skipping and line.startswith("  - "):
        continue
    skipping = False
    out.append(line)
obj["data"]["elasticsearch.yml"] = "\n".join(out) + "\n"
for key in ("managedFields", "resourceVersion", "uid", "creationTimestamp"):
    obj.get("metadata", {}).pop(key, None)
print(json.dumps(obj))
' "$ns" "$prefix" | kubectl -n "$ns" apply -f -
kubectl -n "$ns" rollout restart "statefulset/$prefix"
kubectl -n "$ns" rollout status "statefulset/$prefix" --timeout=900s

for i in $(seq 1 60); do
  nodes=$(kubectl -n "$ns" exec curl-test -- curl -fsS --max-time 5 \
    "http://${service}:9200/_cluster/health?wait_for_status=yellow&wait_for_nodes=3&timeout=5s" \
    2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["number_of_nodes"])' \
    2>/dev/null || true)
  if [ "$nodes" = "3" ]; then
    printf 'repaired restart discovery settings\n' > submit.txt
    exit 0
  fi
  sleep 5
done

exit 1
