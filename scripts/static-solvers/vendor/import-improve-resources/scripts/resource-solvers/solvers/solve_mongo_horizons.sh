#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongo-rs}"
prefix="${BENCH_PARAM_EXTERNAL_HOST_PREFIX:-domain-rs}"
port="${BENCH_PARAM_NODEPORT_START:-31181}"
kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet --eval "
cfg=rs.conf();
cfg.members.forEach((m,i) => { m.horizons={horizon1:'${prefix}-'+(i+1)+':'+(${port}+i)}; });
cfg.version=(cfg.version||1)+1;
rs.reconfig(cfg);
"
for _ in $(seq 1 60); do
  kubectl -n "$ns" exec mongo-client -- mongosh --quiet \
    "mongodb://${prefix}-1:${port},${prefix}-2:$((port+1)),${prefix}-3:$((port+2))/admin?replicaSet=${BENCH_PARAM_REPLICA_SET_NAME:-rs0}" \
    --eval 'db.hello().ok' | grep -qx 1 && break
  sleep 3
done
printf 'configured MongoDB horizons\n' > submit.txt
