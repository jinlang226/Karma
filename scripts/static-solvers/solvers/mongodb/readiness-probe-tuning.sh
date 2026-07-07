#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/readiness-probe-tuning
# Strategy: native_shell
# Notes: Add the case's probe-script mounts when a workflow-inherited cluster is
# missing them, then tune the live probes in place without disturbing auth/TLS.

static_solver_export_namespace_if_unset "mongodb"

ns="${BENCH_NAMESPACE}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
health_secret="${BENCH_PARAM_HEALTH_SECRET_NAME:-health-user-password}"
health_user="${BENCH_PARAM_HEALTH_USERNAME:-health-user}"
tuned_readiness_initial_delay="${BENCH_PARAM_TUNED_READINESS_INITIAL_DELAY:-20}"
tuned_readiness_timeout="${BENCH_PARAM_TUNED_READINESS_TIMEOUT:-5}"
tuned_readiness_failure_threshold="${BENCH_PARAM_TUNED_READINESS_FAILURE_THRESHOLD:-6}"
tuned_liveness_initial_delay="${BENCH_PARAM_TUNED_LIVENESS_INITIAL_DELAY:-120}"
tuned_liveness_timeout="${BENCH_PARAM_TUNED_LIVENESS_TIMEOUT:-5}"
tuned_liveness_failure_threshold="${BENCH_PARAM_TUNED_LIVENESS_FAILURE_THRESHOLD:-10}"
probe_configmap="${BENCH_PARAM_HEALTH_SCRIPT_CONFIGMAP_NAME:-health-check-script}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
statefulset_json="${tmp_dir}/statefulset.json"

kubectl -n "${ns}" apply -f \
  "${STATIC_SOLVER_REPO_ROOT}/cases/mongodb/readiness-probe-tuning/resource/probe-script.yaml"
kubectl -n "${ns}" get statefulset "${cluster}" -o json > "${statefulset_json}"

expected_replicas="$(
  python3 - "${statefulset_json}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
print(((payload.get("spec") or {}).get("replicas")) or 0)
PY
)"

admin_password="$(
  kubectl -n "${ns}" get secret "${admin_secret}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
)"
[[ -n "${admin_password}" ]] || static_solver_fail "unable to read admin password from secret ${admin_secret}"
health_password="$(
  kubectl -n "${ns}" get secret "${health_secret}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
)"
[[ -n "${health_password}" ]] || static_solver_fail "unable to read health password from secret ${health_secret}"
mongo_uri="mongodb://${admin_user}:${admin_password}@localhost:27017/admin?directConnection=true"

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
mongosh_base+=("${mongo_uri}")

mongo_eval() {
  local pod="${1:?pod is required}"
  local eval_str="${2:?eval string is required}"
  kubectl -n "${ns}" exec "${pod}" -- "${mongosh_base[@]}" --eval "${eval_str}"
}

find_primary() {
  local ordinal pod
  for ((ordinal = 0; ordinal < expected_replicas; ordinal++)); do
    pod="${cluster}-${ordinal}"
    if mongo_eval "${pod}" 'db.hello().isWritablePrimary' 2>/dev/null | grep -q true; then
      printf '%s\n' "${pod}"
      return 0
    fi
  done
  return 1
}

primary_pod=""
for _ in $(seq 1 60); do
  primary_pod="$(find_primary || true)"
  if [[ -n "${primary_pod}" ]]; then
    break
  fi
  sleep 3
done
[[ -n "${primary_pod}" ]] || static_solver_fail "unable to locate a writable primary before probe tuning"
mongo_eval "${primary_pod}" "
  const admin = db.getSiblingDB('admin');
  if (admin.getUser('${health_user}')) {
    admin.updateUser('${health_user}', {pwd: '${health_password}', roles: [{role: 'clusterMonitor', db: 'admin'}]});
  } else {
    admin.createUser({user: '${health_user}', pwd: '${health_password}', roles: [{role: 'clusterMonitor', db: 'admin'}]});
  }
" >/dev/null

python3 - "${statefulset_json}" "${probe_configmap}" "${health_secret}" \
  "${tuned_readiness_initial_delay}" "${tuned_readiness_timeout}" "${tuned_readiness_failure_threshold}" \
  "${tuned_liveness_initial_delay}" "${tuned_liveness_timeout}" "${tuned_liveness_failure_threshold}" <<'PY' | kubectl -n "${BENCH_NAMESPACE}" apply -f -
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
probe_configmap = sys.argv[2]
health_secret = sys.argv[3]
readiness_initial_delay = int(sys.argv[4])
readiness_timeout = int(sys.argv[5])
readiness_failures = int(sys.argv[6])
liveness_initial_delay = int(sys.argv[7])
liveness_timeout = int(sys.argv[8])
liveness_failures = int(sys.argv[9])

payload.pop("status", None)
metadata = payload.get("metadata", {})
for key in ("creationTimestamp", "generation", "managedFields", "resourceVersion", "uid"):
    metadata.pop(key, None)

spec = payload["spec"]["template"]["spec"]
container = spec["containers"][0]

mounts = [
    mount
    for mount in container.get("volumeMounts", [])
    if mount.get("name") not in {"health-script", "health-secret"}
]
mounts += [
    {
        "name": "health-script",
        "mountPath": "/usr/local/bin/probe.sh",
        "subPath": "probe.sh",
        "readOnly": True,
    },
    {
        "name": "health-secret",
        "mountPath": "/etc/health",
        "readOnly": True,
    },
]
container["volumeMounts"] = mounts

volumes = [
    volume
    for volume in spec.get("volumes", [])
    if volume.get("name") not in {"health-script", "health-secret"}
]
volumes += [
    {
        "name": "health-script",
        "configMap": {
            "name": probe_configmap,
            "defaultMode": 493,
        },
    },
    {
        "name": "health-secret",
        "secret": {
            "secretName": health_secret,
            "items": [{"key": "password", "path": "health-password"}],
        },
    },
]
spec["volumes"] = volumes

container["readinessProbe"] = {
    "exec": {"command": ["/usr/local/bin/probe.sh"]},
    "initialDelaySeconds": readiness_initial_delay,
    "periodSeconds": 5,
    "timeoutSeconds": readiness_timeout,
    "failureThreshold": readiness_failures,
}
container["livenessProbe"] = {
    "exec": {"command": ["/usr/local/bin/probe.sh"]},
    "initialDelaySeconds": liveness_initial_delay,
    "periodSeconds": 10,
    "timeoutSeconds": liveness_timeout,
    "failureThreshold": liveness_failures,
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

primary_pod=""
for _ in $(seq 1 120); do
  primary_pod="$(find_primary || true)"
  if [[ -n "${primary_pod}" ]]; then
    healthy_members="$(
      mongo_eval "${primary_pod}" \
        'rs.status().members.filter(m=>m.stateStr==="PRIMARY"||m.stateStr==="SECONDARY").length' 2>/dev/null || true
    )"
    if [[ "${healthy_members}" = "${expected_replicas}" ]]; then
      static_solver_write_submit "tuned MongoDB probes"
      exit 0
    fi
  fi
  sleep 5
done

static_solver_fail "MongoDB replica set did not return to a healthy topology after probe tuning"
