#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/health-check-recovery
# Strategy: native_shell
# Notes: Restore the missing health-check fixture additively on inherited
# clusters, including the auth/keyfile probe path the case oracle expects.

static_solver_export_namespace_if_unset "mongodb"

ns="${BENCH_NAMESPACE}"
configured_cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
health_secret="${BENCH_PARAM_HEALTH_SECRET_NAME:-health-user-password}"
keyfile_secret="${BENCH_PARAM_KEYFILE_SECRET_NAME:-mongo-keyfile}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
health_user="${BENCH_PARAM_HEALTH_USERNAME:-health-user}"
default_admin_password="${BENCH_PARAM_ADMIN_PASSWORD:-admin123password}"
default_health_password="${BENCH_PARAM_HEALTH_PASSWORD:-healthpass123}"
default_keyfile_value="${BENCH_PARAM_COMPATIBLE_KEYFILE_VALUE:-mongoKeyfile0123456789ABCDEF}"
keyfile_path="${BENCH_PARAM_KEYFILE_PATH:-/etc/mongo-keyfile/keyfile}"
probe_script_configmap="${BENCH_PARAM_HEALTH_SCRIPT_CONFIGMAP_NAME:-health-check-script}"
override_configmap="${BENCH_PARAM_HEALTH_OVERRIDES_CONFIGMAP_NAME:-health-overrides}"
probe_script_path="${BENCH_PARAM_HEALTH_SCRIPT_PATH:-/usr/local/bin/health-check.sh}"

cluster="${configured_cluster}"
if ! kubectl -n "${ns}" get statefulset "${cluster}" >/dev/null 2>&1; then
  cluster="$(kubectl -n "${ns}" get statefulset -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
fi
[[ -n "${cluster}" ]] || static_solver_fail "unable to locate a MongoDB statefulset in namespace ${ns}"

replica_set_name="${BENCH_PARAM_REPLICA_SET_NAME:-${cluster}}"
override_member="${BENCH_PARAM_HEALTH_OVERRIDE_MEMBER_NAME:-${cluster}-1}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
statefulset_json="${tmp_dir}/statefulset.json"
probe_script_file="${tmp_dir}/health-check.sh"
keyfile_file="${tmp_dir}/mongo-keyfile"

kubectl -n "${ns}" get statefulset "${cluster}" -o json > "${statefulset_json}"
expected_replicas="$(
  python3 - "${statefulset_json}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
print(((payload.get("spec") or {}).get("replicas")) or 3)
PY
)"

read_secret_password() {
  local secret_name="${1:?secret name is required}"
  kubectl -n "${ns}" get secret "${secret_name}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
}

ensure_secret_password() {
  local secret_name="${1:?secret name is required}"
  local default_password="${2:?default password is required}"
  local current_password=""

  current_password="$(read_secret_password "${secret_name}")"
  if [[ -n "${current_password}" ]]; then
    printf '%s\n' "${current_password}"
    return 0
  fi

  kubectl -n "${ns}" create secret generic "${secret_name}" \
    --from-literal=password="${default_password}" \
    --dry-run=client -o yaml | kubectl -n "${ns}" apply -f - >/dev/null
  printf '%s\n' "${default_password}"
}

admin_password="$(ensure_secret_password "${admin_secret}" "${default_admin_password}")"
health_password="$(ensure_secret_password "${health_secret}" "${default_health_password}")"

mongo_tls_flags=()
for ca_path in /etc/tls/ca.crt /etc/mongo-ca/ca.crt /etc/mongodb/tls/ca.crt /etc/ssl/mongodb/ca.crt; do
  if kubectl -n "${ns}" exec "${cluster}-0" -- /bin/sh -c "test -f ${ca_path}" >/dev/null 2>&1; then
    mongo_tls_flags=(--tls --tlsAllowInvalidHostnames --tlsAllowInvalidCertificates --tlsCAFile "${ca_path}")
    for client_pem in /etc/tls/client.pem /etc/mongo-ca/client.pem; do
      if kubectl -n "${ns}" exec "${cluster}-0" -- /bin/sh -c "test -f ${client_pem}" >/dev/null 2>&1; then
        mongo_tls_flags+=(--tlsCertificateKeyFile "${client_pem}")
        break
      fi
    done
    break
  fi
done

mongosh_base=(mongosh --quiet)
if [[ ${#mongo_tls_flags[@]} -gt 0 ]]; then
  mongosh_base+=("${mongo_tls_flags[@]}")
fi

mongosh_plain_eval() {
  local pod="${1:?pod is required}"
  local eval_str="${2:?eval string is required}"
  kubectl -n "${ns}" exec "${pod}" -- "${mongosh_base[@]}" --eval "${eval_str}"
}

mongosh_uri_eval() {
  local pod="${1:?pod is required}"
  local uri="${2:?uri is required}"
  local eval_str="${3:?eval string is required}"
  kubectl -n "${ns}" exec "${pod}" -- "${mongosh_base[@]}" "${uri}" --eval "${eval_str}"
}

admin_auth_works() {
  local password="${1:-}"
  [[ -n "${password}" ]] || return 1
  local uri="mongodb://${admin_user}:${password}@localhost:27017/admin?directConnection=true"
  mongosh_uri_eval "${cluster}-0" "${uri}" 'db.runCommand({ping:1}).ok' 2>/dev/null | grep -q 1
}

desired_admin_password="${admin_password}"
if ! admin_auth_works "${desired_admin_password}" && admin_auth_works "${default_admin_password}"; then
  desired_admin_password="${default_admin_password}"
fi
admin_uri="mongodb://${admin_user}:${desired_admin_password}@localhost:27017/admin?directConnection=true"

find_primary() {
  local ordinal pod
  for ((ordinal = 0; ordinal < expected_replicas; ordinal++)); do
    pod="${cluster}-${ordinal}"
    if mongosh_plain_eval "${pod}" 'db.hello().isWritablePrimary' 2>/dev/null | grep -q true; then
      printf '%s\n' "${pod}"
      return 0
    fi
    if mongosh_uri_eval "${pod}" "${admin_uri}" 'db.hello().isWritablePrimary' 2>/dev/null | grep -q true; then
      printf '%s\n' "${pod}"
      return 0
    fi
  done
  return 1
}

resolve_keyfile_content() {
  local ordinal pod content=""
  for ((ordinal = 0; ordinal < expected_replicas; ordinal++)); do
    pod="${cluster}-${ordinal}"
    content="$(kubectl -n "${ns}" exec "${pod}" -- cat "${keyfile_path}" 2>/dev/null || true)"
    if [[ -n "${content}" ]]; then
      printf '%s\n' "${content}"
      return 0
    fi
  done

  content="$(
    kubectl -n "${ns}" get secret "${keyfile_secret}" -o jsonpath='{.data.keyfile}' 2>/dev/null | base64 -d 2>/dev/null || true
  )"
  if [[ -n "${content}" ]]; then
    printf '%s\n' "${content}"
    return 0
  fi

  printf '%s\n' "${default_keyfile_value}"
}

printf '%s\n' "$(resolve_keyfile_content)" > "${keyfile_file}"
kubectl -n "${ns}" create secret generic "${keyfile_secret}" \
  --from-file=keyfile="${keyfile_file}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f - >/dev/null

primary_pod=""
for _ in $(seq 1 60); do
  primary_pod="$(find_primary || true)"
  if [[ -n "${primary_pod}" ]]; then
    break
  fi
  sleep 3
done
[[ -n "${primary_pod}" ]] || static_solver_fail "unable to locate a writable primary before health-check reconciliation"

mongosh_uri_eval "${primary_pod}" "${admin_uri}" "
  const admin = db.getSiblingDB('admin');

  function mergeRole(roles, role, dbName) {
    if (!roles.some(item => item.role === role && item.db === dbName)) {
      roles.push({role, db: dbName});
    }
    return roles;
  }

  const adminExisting = admin.getUser('${admin_user}');
  let adminRoles = adminExisting && Array.isArray(adminExisting.roles) ? adminExisting.roles.slice() : [];
  if (adminRoles.length === 0) {
    adminRoles = [
      {role: 'clusterAdmin', db: 'admin'},
      {role: 'userAdminAnyDatabase', db: 'admin'},
      {role: 'readWriteAnyDatabase', db: 'admin'}
    ];
  } else {
    adminRoles = mergeRole(adminRoles, 'clusterAdmin', 'admin');
    adminRoles = mergeRole(adminRoles, 'userAdminAnyDatabase', 'admin');
    adminRoles = mergeRole(adminRoles, 'readWriteAnyDatabase', 'admin');
  }
  if (adminExisting) {
    admin.updateUser('${admin_user}', {pwd: '${desired_admin_password}', roles: adminRoles});
  } else {
    admin.createUser({user: '${admin_user}', pwd: '${desired_admin_password}', roles: adminRoles});
  }

  const healthExisting = admin.getUser('${health_user}');
  let healthRoles = healthExisting && Array.isArray(healthExisting.roles) ? healthExisting.roles.slice() : [];
  if (!healthRoles.some(item => item.role === 'clusterMonitor' && item.db === 'admin')) {
    healthRoles.push({role: 'clusterMonitor', db: 'admin'});
  }
  if (healthExisting) {
    admin.updateUser('${health_user}', {pwd: '${health_password}', roles: healthRoles});
  } else {
    admin.createUser({user: '${health_user}', pwd: '${health_password}', roles: healthRoles});
  }
" >/dev/null

kubectl -n "${ns}" create secret generic "${admin_secret}" \
  --from-literal=password="${desired_admin_password}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f - >/dev/null
kubectl -n "${ns}" create secret generic "${health_secret}" \
  --from-literal=password="${health_password}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f - >/dev/null

cat > "${probe_script_file}" <<EOF
#!/bin/sh
set -e

PW_FILE="/etc/health/health-password"
OVERRIDE="/etc/health/overrides/\${POD_NAME}"
if [ -f "\${OVERRIDE}" ]; then
  HEALTH_PW="\$(cat "\${OVERRIDE}")"
else
  HEALTH_PW="\$(cat "\${PW_FILE}")"
fi

set -- mongosh --quiet
for ca in /etc/tls/ca.crt /etc/mongo-ca/ca.crt /etc/mongodb/tls/ca.crt /etc/ssl/mongodb/ca.crt; do
  if [ -f "\${ca}" ]; then
    set -- "\$@" --tls --tlsAllowInvalidHostnames --tlsAllowInvalidCertificates --tlsCAFile "\${ca}"
    for client in /etc/tls/client.pem /etc/mongo-ca/client.pem; do
      if [ -f "\${client}" ]; then
        set -- "\$@" --tlsCertificateKeyFile "\${client}"
        break
      fi
    done
    break
  fi
done

set -- "\$@" "mongodb://${health_user}:\${HEALTH_PW}@localhost:27017/admin?directConnection=true"
"\$@" --eval 'db.hello().ok' >/dev/null
EOF

kubectl -n "${ns}" create configmap "${probe_script_configmap}" \
  --from-file=health-check.sh="${probe_script_file}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f - >/dev/null
kubectl -n "${ns}" create configmap "${override_configmap}" \
  --from-literal="${override_member}=${health_password}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f - >/dev/null

python3 - "${statefulset_json}" "${replica_set_name}" "${keyfile_secret}" "${keyfile_path}" \
  "${health_secret}" "${probe_script_configmap}" "${override_configmap}" "${probe_script_path}" <<'PY' | \
  kubectl -n "${ns}" apply -f - >/dev/null
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
replica_set_name = sys.argv[2]
keyfile_secret = sys.argv[3]
keyfile_path = sys.argv[4]
health_secret = sys.argv[5]
probe_configmap = sys.argv[6]
override_configmap = sys.argv[7]
probe_script_path = sys.argv[8]

payload.pop("status", None)
metadata = payload.get("metadata", {})
for key in ("creationTimestamp", "generation", "managedFields", "resourceVersion", "uid"):
    metadata.pop(key, None)

spec = payload["spec"]["template"]["spec"]
spec["securityContext"] = {"runAsUser": 0, "runAsGroup": 0}
container = spec["containers"][0]

original_command = container.get("command") or ["mongod", "--replSet", replica_set_name, "--bind_ip_all"]
command: list[str] = []
idx = 0
has_replset = False
has_bind_ip = False
while idx < len(original_command):
    item = str(original_command[idx])
    if item == "--replSet":
        has_replset = True
        command.extend(["--replSet", replica_set_name])
        idx += 2
        continue
    if item.startswith("--replSet="):
        has_replset = True
        command.append(f"--replSet={replica_set_name}")
        idx += 1
        continue
    if item == "--keyFile":
        idx += 2
        continue
    if item.startswith("--keyFile="):
        idx += 1
        continue
    if item == "--auth":
        idx += 1
        continue
    if item == "--bind_ip_all" or item.startswith("--bind_ip="):
        has_bind_ip = True
    command.append(item)
    idx += 1

if not command:
    command = ["mongod"]
if command[0] != "mongod":
    command.insert(0, "mongod")
if not has_replset:
    command.extend(["--replSet", replica_set_name])
if not has_bind_ip:
    command.append("--bind_ip_all")
command.append("--auth")
command.append(f"--keyFile={keyfile_path}")
container["command"] = command

container["env"] = [
    env for env in container.get("env", []) if env.get("name") != "POD_NAME"
] + [
    {
        "name": "POD_NAME",
        "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}},
    }
]

container["volumeMounts"] = [
    mount
    for mount in container.get("volumeMounts", [])
    if mount.get("name") not in {"mongo-keyfile", "health-script", "health-secret", "health-overrides"}
] + [
    {
        "name": "mongo-keyfile",
        "mountPath": "/etc/mongo-keyfile",
        "readOnly": True,
    },
    {
        "name": "health-script",
        "mountPath": probe_script_path,
        "subPath": "health-check.sh",
        "readOnly": True,
    },
    {
        "name": "health-secret",
        "mountPath": "/etc/health",
        "readOnly": True,
    },
    {
        "name": "health-overrides",
        "mountPath": "/etc/health/overrides",
        "readOnly": True,
    },
]

spec["volumes"] = [
    volume
    for volume in spec.get("volumes", [])
    if volume.get("name") not in {"mongo-keyfile", "health-script", "health-secret", "health-overrides"}
] + [
    {
        "name": "mongo-keyfile",
        "secret": {"secretName": keyfile_secret, "defaultMode": 256},
    },
    {
        "name": "health-script",
        "configMap": {"name": probe_configmap, "defaultMode": 493},
    },
    {
        "name": "health-secret",
        "secret": {
            "secretName": health_secret,
            "items": [{"key": "password", "path": "health-password"}],
        },
    },
    {
        "name": "health-overrides",
        "configMap": {"name": override_configmap},
    },
]

container["readinessProbe"] = {
    "exec": {"command": [probe_script_path]},
    "initialDelaySeconds": 60,
    "periodSeconds": 10,
    "timeoutSeconds": 5,
    "failureThreshold": 6,
}
container["livenessProbe"] = {
    "exec": {"command": [probe_script_path]},
    "initialDelaySeconds": 90,
    "periodSeconds": 10,
    "timeoutSeconds": 5,
    "failureThreshold": 10,
}

print(json.dumps(payload))
PY

if ! kubectl -n "${ns}" rollout status "statefulset/${cluster}" --timeout=900s; then
  static_solver_log "rollout status reported a transient error; polling pod readiness directly"
fi

python3 - "${ns}" "${cluster}" "${expected_replicas}" <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
import time

ns, cluster, expected = sys.argv[1], sys.argv[2], int(sys.argv[3])
deadline = time.monotonic() + 900

while time.monotonic() < deadline:
    sts_res = subprocess.run(
        ["kubectl", "-n", ns, "get", "statefulset", cluster, "-o", "json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if sts_res.returncode != 0:
        time.sleep(5)
        continue
    try:
        sts = json.loads(sts_res.stdout)
    except json.JSONDecodeError:
        time.sleep(5)
        continue

    status = sts.get("status", {}) or {}
    ready = status.get("readyReplicas") or 0
    updated = status.get("updatedReplicas") or 0
    current_revision = status.get("currentRevision")
    update_revision = status.get("updateRevision")
    if ready != expected or updated != expected or not current_revision or current_revision != update_revision:
        time.sleep(5)
        continue

    pods_res = subprocess.run(
        ["kubectl", "-n", ns, "get", "pods", "-l", f"app={cluster}", "-o", "json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if pods_res.returncode != 0:
        time.sleep(5)
        continue
    try:
        pods = json.loads(pods_res.stdout)
    except json.JSONDecodeError:
        time.sleep(5)
        continue

    items = pods.get("items", [])
    ready_pods = 0
    for item in items:
        conditions = (item.get("status", {}) or {}).get("conditions", []) or []
        if any(cond.get("type") == "Ready" and cond.get("status") == "True" for cond in conditions):
            ready_pods += 1

    if len(items) == expected and ready_pods == expected:
        raise SystemExit(0)

    time.sleep(5)

print(f"timed out waiting for {expected} ready pods for {cluster} in {ns}", file=sys.stderr)
raise SystemExit(1)
PY

for ((ordinal = 0; ordinal < expected_replicas; ordinal++)); do
  kubectl -n "${ns}" exec "${cluster}-${ordinal}" -- "${probe_script_path}" >/dev/null
done

primary_pod=""
for _ in $(seq 1 120); do
  primary_pod="$(find_primary || true)"
  if [[ -n "${primary_pod}" ]]; then
    healthy_members="$(
      mongosh_uri_eval "${primary_pod}" "${admin_uri}" \
        'rs.status().members.filter(m=>m.stateStr==="PRIMARY"||m.stateStr==="SECONDARY").length' \
        2>/dev/null | tr -dc '0-9'
    )"
    if [[ "${healthy_members}" == "${expected_replicas}" ]]; then
      static_solver_write_submit "restored MongoDB health-check probes"
      exit 0
    fi
  fi
  sleep 5
done

static_solver_fail "MongoDB replica set did not return to a healthy topology after health-check reconciliation"
