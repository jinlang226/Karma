#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/mongod-config-update
# Strategy: native_shell
# Notes: Update the live mongod config in place so workflow-inherited settings
# such as TLS remain intact while the expected observability/storage knobs change.

static_solver_export_namespace_if_unset "mongodb"

ns="${BENCH_NAMESPACE}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
replica_set="${BENCH_PARAM_REPLICA_SET_NAME:-mongodb-replica}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
seed_database="${BENCH_PARAM_SEED_DATABASE:-testdb}"
seed_collection="${BENCH_PARAM_SEED_COLLECTION:-data}"
target_journal_compressor="${BENCH_PARAM_TARGET_JOURNAL_COMPRESSOR:-zlib}"
default_admin_password="${BENCH_PARAM_ADMIN_PASSWORD:-admin123password}"

mounted_configmap="$(
  kubectl -n "${ns}" get statefulset "${cluster}" \
    -o jsonpath='{range .spec.template.spec.volumes[*]}{.name}{"="}{.configMap.name}{"\n"}{end}' 2>/dev/null \
    | awk -F= '$1=="mongod-config"{print $2; exit}'
)"
configmap_name="${BENCH_PARAM_MONGOD_CONFIGMAP_NAME:-${BENCH_PARAM_CONFIGMAP_NAME:-${mounted_configmap:-mongod-config}}}"
search_replicas="$(
  kubectl -n "${ns}" get statefulset "${cluster}" -o jsonpath='{.spec.replicas}' 2>/dev/null || printf '3'
)"
expected_replicas=""

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
updated_conf="${tmp_dir}/mongod-updated.conf"
statefulset_json="${tmp_dir}/statefulset.json"
cmdline_json="${tmp_dir}/cmdline.json"

secret_admin_password="$(
  kubectl -n "${ns}" get secret "${admin_secret}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
)"
admin_password=""

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

kubectl -n "${ns}" get statefulset "${cluster}" -o json > "${statefulset_json}"
mongosh_base=()

build_mongosh_base() {
  local password="${1:?password is required}"
  local mongo_uri="mongodb://${admin_user}:${password}@localhost:27017/admin?directConnection=true"

  mongosh_base=(mongosh --quiet)
  if [[ ${#mongo_tls_flags[@]} -gt 0 ]]; then
    mongosh_base+=("${mongo_tls_flags[@]}")
  fi
  mongosh_base+=("${mongo_uri}")
}

mongo_eval_with_password() {
  local password="${1:?password is required}"
  local pod="${2:?pod is required}"
  local eval_str="${3:?eval string is required}"

  build_mongosh_base "${password}"
  kubectl -n "${ns}" exec "${pod}" -- "${mongosh_base[@]}" --eval "${eval_str}"
}

mongo_eval() {
  local pod="${1:?pod is required}"
  local eval_str="${2:?eval string is required}"
  kubectl -n "${ns}" exec "${pod}" -- "${mongosh_base[@]}" --eval "${eval_str}"
}

find_primary_with_password() {
  local password="${1:?password is required}"
  local ordinal pod
  for ((ordinal = 0; ordinal < search_replicas; ordinal++)); do
    pod="${cluster}-${ordinal}"
    if mongo_eval_with_password "${password}" "${pod}" 'db.hello().isWritablePrimary' 2>/dev/null | grep -q true; then
      printf '%s\n' "${pod}"
      return 0
    fi
  done
  return 1
}

repair_admin_secret() {
  local password="${1:?password is required}"
  kubectl -n "${ns}" create secret generic "${admin_secret}" \
    --from-literal=password="${password}" \
    --dry-run=client -o yaml | kubectl -n "${ns}" apply -f - >/dev/null
}

resolve_admin_access() {
  local candidate primary
  local -a candidates=()

  if [[ -n "${secret_admin_password}" ]]; then
    candidates+=("${secret_admin_password}")
  fi
  if [[ "${default_admin_password}" != "${secret_admin_password}" ]]; then
    candidates+=("${default_admin_password}")
  fi

  for candidate in "${candidates[@]}"; do
    primary="$(find_primary_with_password "${candidate}" || true)"
    if [[ -z "${primary}" ]]; then
      continue
    fi

    admin_password="${candidate}"
    build_mongosh_base "${admin_password}"
    primary_pod="${primary}"
    if [[ "${secret_admin_password}" != "${admin_password}" ]]; then
      static_solver_log "restoring ${admin_secret} from working MongoDB credentials"
      repair_admin_secret "${admin_password}"
      secret_admin_password="${admin_password}"
    fi
    return 0
  done

  return 1
}

primary_pod=""
for _ in $(seq 1 60); do
  if resolve_admin_access; then
    break
  fi
  sleep 3
done
[[ -n "${primary_pod}" ]] || static_solver_fail "unable to locate a writable primary before updating mongod config"

replica_configured_count="$(
  mongo_eval "${primary_pod}" 'rs.conf().members.length' 2>/dev/null || true
)"
if [[ "${replica_configured_count}" =~ ^[0-9]+$ ]] && (( replica_configured_count > 0 )); then
  expected_replicas="${BENCH_PARAM_RESTORE_REPLICAS:-${BENCH_PARAM_EXPECTED_REPLICAS:-${replica_configured_count}}}"
else
  expected_replicas="${BENCH_PARAM_RESTORE_REPLICAS:-${BENCH_PARAM_EXPECTED_REPLICAS:-${search_replicas}}}"
fi

mongo_eval "${primary_pod}" \
  "const coll=db.getSiblingDB('${seed_database}').getCollection('${seed_collection}'); if (coll.countDocuments({}) < 1) { coll.insertOne({config:'initial'}); }" >/dev/null
mongo_eval "${primary_pod}" 'print(EJSON.stringify(db.adminCommand({getCmdLineOpts:1}), null, 2))' > "${cmdline_json}"
[[ -s "${cmdline_json}" ]] || static_solver_fail "unable to capture getCmdLineOpts from ${primary_pod}"

python3 - "${cmdline_json}" "${updated_conf}" "${replica_set}" "${target_journal_compressor}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml


def ensure_dict(parent: dict, key: str) -> dict:
    """Return a nested mapping, creating it when missing."""
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


current_path = Path(sys.argv[1])
updated_path = Path(sys.argv[2])
replica_set = sys.argv[3]
target_journal_compressor = sys.argv[4]

payload = json.loads(current_path.read_text())
parsed = payload.get("parsed") or {}
if not isinstance(parsed, dict):
    raise SystemExit("getCmdLineOpts payload missing parsed config")
parsed.pop("config", None)

storage = ensure_dict(parsed, "storage")
storage.setdefault("dbPath", "/data/db")
wired_tiger = ensure_dict(storage, "wiredTiger")
engine_config = ensure_dict(wired_tiger, "engineConfig")
engine_config["journalCompressor"] = target_journal_compressor

net = ensure_dict(parsed, "net")
net.setdefault("bindIpAll", True)

replication = ensure_dict(parsed, "replication")
legacy_repl_set = replication.pop("replSet", None)
replication["replSetName"] = str(replication.get("replSetName") or legacy_repl_set or replica_set)

security = ensure_dict(parsed, "security")
security.setdefault("authorization", "enabled")
security.setdefault("keyFile", "/etc/mongo-keyfile/keyfile")

system_log = ensure_dict(parsed, "systemLog")
current_verbosity = int(system_log.get("verbosity") or 0)
system_log["verbosity"] = current_verbosity + 1

profiling = ensure_dict(parsed, "operationProfiling")
profiling["mode"] = "slowOp"
current_slow_ms = int(profiling.get("slowOpThresholdMs") or 100)
profiling["slowOpThresholdMs"] = current_slow_ms * 2

updated_path.write_text(yaml.safe_dump(parsed, sort_keys=False))
PY

kubectl -n "${ns}" create configmap "${configmap_name}" \
  --from-file=mongod.conf="${updated_conf}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

if [[ -z "${mounted_configmap}" ]]; then
  patch_json="$(
    python3 - "${statefulset_json}" "${configmap_name}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

statefulset = json.loads(Path(sys.argv[1]).read_text())
configmap_name = sys.argv[2]
template_spec = (((statefulset.get("spec") or {}).get("template") or {}).get("spec") or {})
containers = template_spec.get("containers") or []
if not containers:
    raise SystemExit("statefulset missing containers")
container = containers[0]

ops: list[dict[str, object]] = []
command_value = ["mongod", "--config", "/etc/mongo-config/mongod.conf"]
if "command" in container:
    ops.append({"op": "replace", "path": "/spec/template/spec/containers/0/command", "value": command_value})
else:
    ops.append({"op": "add", "path": "/spec/template/spec/containers/0/command", "value": command_value})

mount_value = {
    "name": "mongod-config",
    "mountPath": "/etc/mongo-config/mongod.conf",
    "subPath": "mongod.conf",
    "readOnly": True,
}
mounts = container.get("volumeMounts")
if isinstance(mounts, list):
    idx = next((i for i, mount in enumerate(mounts) if mount.get("name") == "mongod-config"), None)
    if idx is None:
        ops.append({"op": "add", "path": "/spec/template/spec/containers/0/volumeMounts/-", "value": mount_value})
    else:
        ops.append({"op": "replace", "path": f"/spec/template/spec/containers/0/volumeMounts/{idx}", "value": mount_value})
else:
    ops.append({"op": "add", "path": "/spec/template/spec/containers/0/volumeMounts", "value": [mount_value]})

volume_value = {
    "name": "mongod-config",
    "configMap": {
        "name": configmap_name,
        "items": [{"key": "mongod.conf", "path": "mongod.conf"}],
    },
}
volumes = template_spec.get("volumes")
if isinstance(volumes, list):
    idx = next((i for i, volume in enumerate(volumes) if volume.get("name") == "mongod-config"), None)
    if idx is None:
        ops.append({"op": "add", "path": "/spec/template/spec/volumes/-", "value": volume_value})
    else:
        ops.append({"op": "replace", "path": f"/spec/template/spec/volumes/{idx}", "value": volume_value})
else:
    ops.append({"op": "add", "path": "/spec/template/spec/volumes", "value": [volume_value]})

print(json.dumps(ops))
PY
  )"
  kubectl -n "${ns}" patch statefulset "${cluster}" --type=json -p "${patch_json}"
else
  kubectl -n "${ns}" rollout restart "statefulset/${cluster}"
fi

kubectl -n "${ns}" scale statefulset "${cluster}" --replicas="${expected_replicas}" >/dev/null

if ! kubectl -n "${ns}" rollout status "statefulset/${cluster}" --timeout=900s; then
  static_solver_log "rollout status reported a transient error; falling back to statefulset and pod readiness polling"
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
    spec_replicas = ((sts.get("spec") or {}).get("replicas")) or 0
    ready = status.get("readyReplicas") or 0
    updated = status.get("updatedReplicas") or 0
    current_revision = status.get("currentRevision")
    update_revision = status.get("updateRevision")

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
    pod_ready = 0
    for item in items:
        conditions = (item.get("status", {}) or {}).get("conditions", []) or []
        if any(cond.get("type") == "Ready" and cond.get("status") == "True" for cond in conditions):
            pod_ready += 1

    if (
        spec_replicas == expected
        and len(items) == expected
        and ready == expected
        and updated == expected
        and current_revision
        and current_revision == update_revision
        and pod_ready == expected
    ):
        raise SystemExit(0)

    time.sleep(5)

print(
    f"timed out waiting for restarted MongoDB statefulset {cluster} in {ns} "
    f"to reach {expected} ready/updated replicas",
    file=sys.stderr,
)
raise SystemExit(1)
PY

static_solver_write_submit "updated mongod configuration"
