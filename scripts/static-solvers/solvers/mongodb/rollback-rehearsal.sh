#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/rollback-rehearsal
# Strategy: native_shell
# Notes: Review-only rollback planner. It captures the live MongoDB state and
# writes a rollback script to ConfigMap/rollback-rehearsal without executing it.

static_solver_export_namespace_if_unset "mongodb"

ns="${BENCH_NAMESPACE}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
replica_set="${BENCH_PARAM_REPLICA_SET_NAME:-mongodb-replica}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
default_log_verbosity="${BENCH_PARAM_DEFAULT_LOG_VERBOSITY:-0}"
default_slow_ms="${BENCH_PARAM_DEFAULT_SLOW_OP_THRESHOLD_MS:-100}"
default_journal_compressor="${BENCH_PARAM_DEFAULT_JOURNAL_COMPRESSOR:-snappy}"

resolve_configmap_name() {
  local candidate=""
  for candidate in \
    "${BENCH_PARAM_MONGOD_CONFIGMAP_NAME:-}" \
    "${BENCH_PARAM_CONFIGMAP_NAME:-}" \
    "${cluster}-mongod-config" \
    "mongod-config"
  do
    [[ -n "${candidate}" ]] || continue
    if kubectl -n "${ns}" get configmap "${candidate}" >/dev/null 2>&1; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  printf '%s\n' "mongod-config"
}

configmap_name="$(resolve_configmap_name)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

admin_password="$(
  kubectl -n "${ns}" get secret "${admin_secret}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
)"
if [[ -z "${admin_password}" ]]; then
  admin_password="${BENCH_PARAM_ADMIN_PASSWORD:-admin123password}"
fi
mongo_uri="mongodb://${admin_user}:${admin_password}@localhost:27017/admin?directConnection=true"

capture_cmd() {
  local name="${1:?output name is required}"
  shift
  "$@" > "${tmp_dir}/${name}" 2>&1 || true
}

capture_mongosh() {
  local name="${1:?output name is required}"
  local eval_str="${2:?mongosh eval string is required}"
  capture_cmd "${name}" \
    kubectl -n "${ns}" exec "${cluster}-0" -- \
    mongosh --quiet "${mongo_uri}" --eval "${eval_str}"
}

capture_cmd pods.txt kubectl -n "${ns}" get pods -o wide
capture_cmd services.txt kubectl -n "${ns}" get svc
capture_cmd statefulset.yaml kubectl -n "${ns}" get statefulset "${cluster}" -o yaml
capture_cmd configmap.yaml kubectl -n "${ns}" get configmap "${configmap_name}" -o yaml
capture_mongosh rs-status.json 'print(EJSON.stringify(rs.status(), null, 2))'
capture_mongosh log-verbosity.json 'print(EJSON.stringify(db.adminCommand({getParameter: 1, logComponentVerbosity: 1}), null, 2))'
capture_mongosh slow-threshold.json 'print(EJSON.stringify(db.adminCommand({getParameter: 1, slowOpThresholdMs: 1}), null, 2))'
capture_mongosh cmdline-opts.json 'print(EJSON.stringify(db.adminCommand({getCmdLineOpts: 1}), null, 2))'

script_path="${STATIC_SOLVER_STAGE_DIR}/rollback.sh"
{
  cat <<EOF
#!/usr/bin/env bash
set -euo pipefail

NS="${ns}"
CLUSTER="${cluster}"
REPLICA_SET="${replica_set}"
CONFIGMAP_NAME="${configmap_name}"
ADMIN_USER="${admin_user}"
ADMIN_SECRET="${admin_secret}"
DEFAULT_LOG_VERBOSITY="${default_log_verbosity}"
DEFAULT_SLOW_OP_THRESHOLD_MS="${default_slow_ms}"
DEFAULT_JOURNAL_COMPRESSOR="${default_journal_compressor}"

ADMIN_PASSWORD="\$(kubectl -n "\${NS}" get secret "\${ADMIN_SECRET}" -o jsonpath='{.data.password}' | base64 -d)"
MONGO_URI="mongodb://\${ADMIN_USER}:\${ADMIN_PASSWORD}@localhost:27017/admin?directConnection=true"

# Review-only rollback rehearsal for MongoDB.
# Generated from the live cluster snapshot below. Review every command before use.

# 1. Capture the live cluster state before any rollback action.
kubectl -n "\${NS}" get pods -o wide
kubectl -n "\${NS}" get svc
kubectl -n "\${NS}" get statefulset "\${CLUSTER}" -o yaml
kubectl -n "\${NS}" exec "\${CLUSTER}-0" -- \
  mongosh --quiet "\${MONGO_URI}" --eval 'print(EJSON.stringify(rs.status(), null, 2))'

# 2. Prepare the default mongod.conf expected for rollback review.
cat > /tmp/mongod-defaults.conf <<EOF_MONGOD_CONF
storage:
  dbPath: /data/db
  wiredTiger:
    engineConfig:
      journalCompressor: \${DEFAULT_JOURNAL_COMPRESSOR}
net:
  bindIpAll: true
replication:
  replSetName: \${REPLICA_SET}
security:
  authorization: enabled
  keyFile: /etc/mongo-keyfile/keyfile
systemLog:
  verbosity: \${DEFAULT_LOG_VERBOSITY}
operationProfiling:
  mode: slowOp
  slowOpThresholdMs: \${DEFAULT_SLOW_OP_THRESHOLD_MS}
EOF_MONGOD_CONF

# 3. Review and, if approved, re-apply the default config map.
kubectl -n "\${NS}" create configmap "\${CONFIGMAP_NAME}" \
  --from-file=mongod.conf=/tmp/mongod-defaults.conf \
  --dry-run=client -o yaml > /tmp/\${CONFIGMAP_NAME}.rollback.yaml
kubectl -n "\${NS}" apply -f /tmp/\${CONFIGMAP_NAME}.rollback.yaml

# 4. Roll the StatefulSet one member at a time and wait for readiness.
kubectl -n "\${NS}" rollout restart statefulset/"\${CLUSTER}"
kubectl -n "\${NS}" rollout status statefulset/"\${CLUSTER}" --timeout=600s

# 5. Re-check topology and current parameters after the reviewed rollback.
kubectl -n "\${NS}" exec "\${CLUSTER}-0" -- \
  mongosh --quiet "\${MONGO_URI}" --eval 'print(EJSON.stringify(rs.status(), null, 2))'
kubectl -n "\${NS}" exec "\${CLUSTER}-0" -- \
  mongosh --quiet "\${MONGO_URI}" --eval 'print(EJSON.stringify(db.adminCommand({getParameter: 1, logComponentVerbosity: 1}), null, 2))'
kubectl -n "\${NS}" exec "\${CLUSTER}-0" -- \
  mongosh --quiet "\${MONGO_URI}" --eval 'print(EJSON.stringify(db.adminCommand({getParameter: 1, slowOpThresholdMs: 1}), null, 2))'

# Captured evidence from the generation point:
EOF

  while IFS= read -r line; do
    printf '# %s\n' "${line}"
  done < "${tmp_dir}/pods.txt"
  while IFS= read -r line; do
    printf '# %s\n' "${line}"
  done < "${tmp_dir}/services.txt"
  while IFS= read -r line; do
    printf '# %s\n' "${line}"
  done < "${tmp_dir}/rs-status.json"
  while IFS= read -r line; do
    printf '# %s\n' "${line}"
  done < "${tmp_dir}/log-verbosity.json"
  while IFS= read -r line; do
    printf '# %s\n' "${line}"
  done < "${tmp_dir}/slow-threshold.json"
  while IFS= read -r line; do
    printf '# %s\n' "${line}"
  done < "${tmp_dir}/cmdline-opts.json"

  cat <<EOF

# Current StatefulSet snapshot for operator review:
cat <<'EOF_STATEFULSET'
$(cat "${tmp_dir}/statefulset.yaml")
EOF_STATEFULSET

# Current config map snapshot for operator review:
cat <<'EOF_CONFIGMAP'
$(cat "${tmp_dir}/configmap.yaml")
EOF_CONFIGMAP
EOF
} > "${script_path}"

kubectl -n "${ns}" create configmap rollback-rehearsal \
  --from-file=rollback.sh="${script_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "prepared rollback-rehearsal ConfigMap"
