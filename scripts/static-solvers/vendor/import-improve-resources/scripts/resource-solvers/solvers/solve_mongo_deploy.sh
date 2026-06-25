#!/bin/sh
set -eu
ns="$BENCH_NAMESPACE"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
service="${BENCH_PARAM_HEADLESS_SERVICE_NAME:-mongodb-replica-svc}"
replicas="${BENCH_PARAM_EXPECTED_REPLICAS:-3}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
app_secret="${BENCH_PARAM_APP_SECRET_NAME:-app-user-password}"
keyfile_secret="${BENCH_PARAM_KEYFILE_SECRET_NAME:-mongo-keyfile}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
app_user="${BENCH_PARAM_APP_USERNAME:-app-user}"
app_db="${BENCH_PARAM_APP_DATABASE:-appdb}"

envsubst '${BENCH_PARAM_ADMIN_SECRET_NAME} ${BENCH_PARAM_APP_SECRET_NAME} ${BENCH_PARAM_KEYFILE_SECRET_NAME}' \
  < resources/mongodb/deploy/resource/secrets.yaml | kubectl -n "$ns" apply -f -
envsubst '${BENCH_PARAM_CLUSTER_PREFIX} ${BENCH_PARAM_HEADLESS_SERVICE_NAME}' \
  < resources/mongodb/deploy/resource/services.yaml | kubectl -n "$ns" apply -f -
envsubst '${BENCH_PARAM_CLUSTER_PREFIX} ${BENCH_PARAM_EXPECTED_REPLICAS} ${BENCH_PARAM_HEADLESS_SERVICE_NAME} ${BENCH_PARAM_KEYFILE_SECRET_NAME} ${BENCH_PARAM_MONGO_IMAGE} ${BENCH_PARAM_REPLICA_SET_NAME}' \
  < resources/mongodb/deploy/resource/statefulset.yaml | kubectl -n "$ns" apply -f -
kubectl -n "$ns" wait --for=jsonpath="{.status.readyReplicas}=${replicas}" "sts/${cluster}" --timeout=600s
python3 resources/mongodb/common/init_replica_set.py
for _ in $(seq 1 60); do
  kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet --eval \
    'db.hello().isWritablePrimary' | grep -qx true && break
  sleep 3
done
admin_pw=$(kubectl -n "$ns" get secret "$admin_secret" -o jsonpath='{.data.password}' | base64 -d)
app_pw=$(kubectl -n "$ns" get secret "$app_secret" -o jsonpath='{.data.password}' | base64 -d)
kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet --eval "
try {
  db.getSiblingDB('admin').createUser({
    user:'${admin_user}', pwd:'${admin_pw}',
    roles:[
      {role:'clusterAdmin',db:'admin'},
      {role:'userAdminAnyDatabase',db:'admin'},
      {role:'readWriteAnyDatabase',db:'admin'}
    ]
  });
} catch (e) {
  if (!String(e).includes('already exists')) throw e;
}
"
kubectl -n "$ns" exec "${cluster}-0" -- mongosh --quiet \
  "mongodb://${admin_user}:${admin_pw}@localhost:27017/admin" --eval "
const app=db.getSiblingDB('${app_db}');
try {
  app.createUser({user:'${app_user}',pwd:'${app_pw}',roles:[{role:'readWrite',db:'${app_db}'}]});
} catch (e) {
  app.updateUser('${app_user}',{pwd:'${app_pw}',roles:[{role:'readWrite',db:'${app_db}'}]});
}
"
printf 'deployed MongoDB replica set\n' > submit.txt
