#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
data="${BENCH_PARAM_DATA_CLUSTER_PREFIX:-mongo-rs}"
arb="${BENCH_PARAM_ARBITER_CLUSTER_PREFIX:-mongo-arb}"
arb_service="${BENCH_PARAM_ARBITER_SERVICE_NAME:-mongo-arb}"
host="${arb}-0.${arb_service}.${ns}.svc.cluster.local:27017"
kubectl -n "$ns" exec "${data}-0" -- mongosh --quiet --eval '
db.adminCommand({
  setDefaultRWConcern: 1,
  defaultReadConcern: {level: "local"},
  defaultWriteConcern: {w: "majority"}
})
'
kubectl -n "$ns" exec "${data}-0" -- mongosh --quiet --eval "
const host='${host}';
const members=rs.conf().members || [];
if (!members.some(m => m.host === host && m.arbiterOnly === true)) {
  rs.addArb(host);
}
"
for _ in $(seq 1 60); do
  kubectl -n "$ns" exec "${data}-0" -- mongosh --quiet --eval \
    "rs.status().members.filter(m => m.stateStr === 'ARBITER').length === 1" |
    grep -qx true && break
  sleep 3
done
printf 'added MongoDB arbiter\n' > submit.txt
