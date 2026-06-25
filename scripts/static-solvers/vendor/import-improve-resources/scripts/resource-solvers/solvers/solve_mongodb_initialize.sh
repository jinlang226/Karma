#!/bin/sh
set -eu

ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
service="${BENCH_PARAM_HEADLESS_SERVICE_NAME:-mongodb-replica-svc}"

kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet --eval "
  cfg=rs.conf();
  cfg.members[1].host=\"${cluster}-1.${service}.${ns}.svc.cluster.local:27017\";
  cfg.version+=1;
  rs.reconfig(cfg);"

printf 'repaired MongoDB replica-set member address\n' > submit.txt
