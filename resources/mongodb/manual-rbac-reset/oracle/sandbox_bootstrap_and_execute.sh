#!/usr/bin/env bash
set -euo pipefail

ns_file="${ORACLE_SANDBOX_NS_FILE:-oracle_sandbox_ns.txt}"

cluster_prefix="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
headless_service="${BENCH_PARAM_HEADLESS_SERVICE_NAME:-mongodb-replica-svc}"
replica_set_name="${BENCH_PARAM_REPLICA_SET_NAME:-mongodb-replica}"
expected_replicas="${BENCH_PARAM_EXPECTED_REPLICAS:-3}"

admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
app_secret="${BENCH_PARAM_APP_SECRET_NAME:-app-user-password}"
reporting_secret="${BENCH_PARAM_REPORTING_SECRET_NAME:-reporting-user-password}"
keyfile_secret="${BENCH_PARAM_KEYFILE_SECRET_NAME:-mongo-keyfile}"

admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
app_user="${BENCH_PARAM_APP_USERNAME:-app-user}"
reporting_user="${BENCH_PARAM_REPORTING_USERNAME:-reporting-user}"
app_db="${BENCH_PARAM_APP_DATABASE:-appdb}"
reports_collection="${BENCH_PARAM_REPORTS_COLLECTION:-reports}"
raw_collection="${BENCH_PARAM_RAW_COLLECTION:-raw}"
bad_role="${BENCH_PARAM_BAD_ROLE_NAME:-rawRead}"
reporting_role="${BENCH_PARAM_REPORTING_ROLE_NAME:-reportingRole}"

sandbox_ns="oracle-rbac-$(date -u +%Y%m%d%H%M%S)-$RANDOM"
echo "${sandbox_ns}" > "${ns_file}"

kubectl get ns "${sandbox_ns}" >/dev/null 2>&1 || kubectl create ns "${sandbox_ns}"

kubectl -n "${sandbox_ns}" create secret generic "${admin_secret}" \
  --from-literal=password=admin123password \
  --dry-run=client -o yaml | kubectl -n "${sandbox_ns}" apply -f -
kubectl -n "${sandbox_ns}" create secret generic "${app_secret}" \
  --from-literal=password=app123password \
  --dry-run=client -o yaml | kubectl -n "${sandbox_ns}" apply -f -
kubectl -n "${sandbox_ns}" create secret generic "${reporting_secret}" \
  --from-literal=password=reporting123password \
  --dry-run=client -o yaml | kubectl -n "${sandbox_ns}" apply -f -
kubectl -n "${sandbox_ns}" create secret generic "${keyfile_secret}" \
  --from-literal=keyfile=mongoKeyfile0123456789ABCDEF \
  --dry-run=client -o yaml | kubectl -n "${sandbox_ns}" apply -f -

cat <<EOF | kubectl -n "${sandbox_ns}" apply -f -
apiVersion: v1
kind: Service
metadata:
  name: ${headless_service}
spec:
  clusterIP: None
  publishNotReadyAddresses: true
  selector:
    app: ${cluster_prefix}
  ports:
    - name: mongodb
      port: 27017
      protocol: TCP
      targetPort: 27017
EOF

cat <<EOF | kubectl -n "${sandbox_ns}" apply -f -
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: ${cluster_prefix}
spec:
  serviceName: ${headless_service}
  replicas: ${expected_replicas}
  selector:
    matchLabels:
      app: ${cluster_prefix}
  template:
    metadata:
      labels:
        app: ${cluster_prefix}
    spec:
      securityContext:
        runAsUser: 0
        runAsGroup: 0
      containers:
        - name: mongod
          image: mongo:6.0.5
          command:
            - mongod
            - --replSet
            - ${replica_set_name}
            - --bind_ip_all
            - --auth
            - --keyFile=/etc/mongo-keyfile/keyfile
          ports:
            - containerPort: 27017
              name: mongodb
              protocol: TCP
          volumeMounts:
            - name: data-volume
              mountPath: /data/db
            - name: mongo-keyfile
              mountPath: /etc/mongo-keyfile
              readOnly: true
          resources:
            requests:
              cpu: "0.5"
              memory: "512Mi"
            limits:
              cpu: "1"
              memory: "1Gi"
      volumes:
        - name: mongo-keyfile
          secret:
            secretName: ${keyfile_secret}
            defaultMode: 256
  volumeClaimTemplates:
    - metadata:
        name: data-volume
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 10Gi
EOF

kubectl -n "${sandbox_ns}" wait \
  --for=jsonpath='{.status.readyReplicas}'="${expected_replicas}" \
  "sts/${cluster_prefix}" --timeout=360s

kubectl -n "${sandbox_ns}" exec "${cluster_prefix}-0" -- mongosh --quiet --eval \
  "rs.initiate({_id:\"${replica_set_name}\",members:[{_id:0,host:\"${cluster_prefix}-0.${headless_service}.${sandbox_ns}.svc.cluster.local:27017\"},{_id:1,host:\"${cluster_prefix}-1.${headless_service}.${sandbox_ns}.svc.cluster.local:27017\"},{_id:2,host:\"${cluster_prefix}-2.${headless_service}.${sandbox_ns}.svc.cluster.local:27017\"}]})"

for _ in $(seq 1 40); do
  if kubectl -n "${sandbox_ns}" exec "${cluster_prefix}-0" -- mongosh --quiet --eval 'db.hello().isWritablePrimary' | grep -qx true; then
    break
  fi
  sleep 3
done

kubectl -n "${sandbox_ns}" exec "${cluster_prefix}-0" -- mongosh --quiet --eval \
  "try { db.getSiblingDB(\"admin\").createUser({user:\"${admin_user}\",pwd:\"admin123password\",roles:[{role:\"clusterAdmin\",db:\"admin\"},{role:\"userAdminAnyDatabase\",db:\"admin\"},{role:\"readWriteAnyDatabase\",db:\"admin\"}]}); } catch (e) { if (!String(e).includes(\"already exists\")) throw e; }"

app_pw_b64="$(kubectl -n "${sandbox_ns}" get secret "${app_secret}" -o jsonpath='{.data.password}')"
reporting_pw_b64="$(kubectl -n "${sandbox_ns}" get secret "${reporting_secret}" -o jsonpath='{.data.password}')"
app_pw="$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode())' "${app_pw_b64}")"
reporting_pw="$(python3 -c 'import base64,sys; print(base64.b64decode(sys.argv[1]).decode())' "${reporting_pw_b64}")"

kubectl -n "${sandbox_ns}" exec "${cluster_prefix}-0" -- mongosh --quiet \
  "mongodb://${admin_user}:admin123password@localhost:27017/admin" --eval \
  "try { db.getSiblingDB(\"${app_db}\").createRole({role:\"${bad_role}\",privileges:[{resource:{db:\"${app_db}\",collection:\"${raw_collection}\"},actions:[\"find\"]}],roles:[]}); } catch (e) { db.getSiblingDB(\"${app_db}\").updateRole(\"${bad_role}\",{privileges:[{resource:{db:\"${app_db}\",collection:\"${raw_collection}\"},actions:[\"find\"]}],roles:[]}); }
   try { db.getSiblingDB(\"admin\").createUser({user:\"${app_user}\",pwd:\"${app_pw}\",roles:[{role:\"read\",db:\"${app_db}\"}]}); } catch (e) { db.getSiblingDB(\"admin\").updateUser(\"${app_user}\",{pwd:\"${app_pw}\",roles:[{role:\"read\",db:\"${app_db}\"}]}); }
   try { db.getSiblingDB(\"admin\").createUser({user:\"${reporting_user}\",pwd:\"${reporting_pw}\",roles:[{role:\"${bad_role}\",db:\"${app_db}\"}]}); } catch (e) { db.getSiblingDB(\"admin\").updateUser(\"${reporting_user}\",{pwd:\"${reporting_pw}\",roles:[{role:\"${bad_role}\",db:\"${app_db}\"}]}); }
   try { db.getSiblingDB(\"admin\").dropRole(\"${reporting_role}\"); } catch (e) {}
   try { db.getSiblingDB(\"${app_db}\").dropRole(\"${reporting_role}\"); } catch (e) {}"

echo "[oracle-sandbox] namespace=${sandbox_ns} bootstrap_ready=true"
