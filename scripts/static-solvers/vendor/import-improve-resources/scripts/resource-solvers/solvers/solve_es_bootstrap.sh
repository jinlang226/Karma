#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"

kubectl -n "$ns" get configmap es-config -o json | python3 -c '
import json, sys
obj = json.load(sys.stdin)
text = obj["data"]["elasticsearch.yml"]
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
' | kubectl -n "$ns" apply -f -
kubectl -n "$ns" rollout restart "statefulset/$prefix"
kubectl -n "$ns" rollout status "statefulset/$prefix" --timeout=900s
printf 'removed bootstrap-only setting\n' > submit.txt
