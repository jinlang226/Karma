#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/statefulset-customization
# Strategy: native_shell
# Notes: Restore the broken StatefulSet template and, when restart or adversary
# drift leaves the replica set without a healthy election, reconfigure the live
# member list back to the expected topology.

static_solver_export_namespace_if_unset "mongodb"

ns="${BENCH_NAMESPACE}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
replica_set="${BENCH_PARAM_REPLICA_SET_NAME:-mongodb-replica}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
label_key="${BENCH_PARAM_TEMPLATE_LABEL_KEY:-monitoring}"
label_value="${BENCH_PARAM_TEMPLATE_LABEL_VALUE:-enabled}"
request_mi="${BENCH_PARAM_MIN_REQUEST_MEMORY_MI:-512}"
limit_mi="${BENCH_PARAM_MIN_LIMIT_MEMORY_MI:-1024}"
target_replicas="${BENCH_PARAM_RESTORE_REPLICAS:-${BENCH_PARAM_EXPECTED_REPLICAS:-3}}"
default_admin_password="${BENCH_PARAM_ADMIN_PASSWORD:-admin123password}"
min_readiness_timeout="${BENCH_PARAM_MIN_READINESS_TIMEOUT:-5}"
min_readiness_failure_threshold="${BENCH_PARAM_MIN_READINESS_FAILURE_THRESHOLD:-6}"
min_liveness_timeout="${BENCH_PARAM_MIN_LIVENESS_TIMEOUT:-5}"
min_liveness_failure_threshold="${BENCH_PARAM_MIN_LIVENESS_FAILURE_THRESHOLD:-10}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
statefulset_json="${tmp_dir}/statefulset.json"

[[ "${target_replicas}" =~ ^[0-9]+$ ]] || static_solver_fail "target replica count must be numeric"
(( target_replicas > 0 )) || static_solver_fail "target replica count must be positive"

service="$(
  kubectl -n "${ns}" get statefulset "${cluster}" -o jsonpath='{.spec.serviceName}' 2>/dev/null || true
)"
service="${service:-${BENCH_PARAM_HEADLESS_SERVICE_NAME:-${cluster}-svc}}"

admin_password="$(
  kubectl -n "${ns}" get secret "${admin_secret}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
)"
admin_password="${admin_password:-${default_admin_password}}"

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
mongosh_base+=("mongodb://${admin_user}:${admin_password}@localhost:27017/admin?directConnection=true")

mongo_eval() {
  local pod="${1:?pod is required}"
  local eval_str="${2:?eval string is required}"
  kubectl -n "${ns}" exec "${pod}" -- "${mongosh_base[@]}" --eval "${eval_str}"
}

find_primary() {
  local ordinal pod
  for ((ordinal = 0; ordinal < target_replicas; ordinal++)); do
    pod="${cluster}-${ordinal}"
    if mongo_eval "${pod}" 'db.hello().isWritablePrimary' 2>/dev/null | grep -q true; then
      printf '%s\n' "${pod}"
      return 0
    fi
  done
  return 1
}

wait_statefulset_ready() {
  python3 - "${ns}" "${cluster}" "${target_replicas}" <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
import time

ns, cluster, expected = sys.argv[1], sys.argv[2], int(sys.argv[3])
deadline = time.monotonic() + 900
last_error = f"timed out waiting for restarted MongoDB statefulset {cluster} in {ns} to reach {expected} ready replicas"

while time.monotonic() < deadline:
    sts_res = subprocess.run(
        ["kubectl", "-n", ns, "get", "statefulset", cluster, "-o", "json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if sts_res.returncode != 0:
        last_error = sts_res.stderr.strip() or "failed to read statefulset"
        time.sleep(5)
        continue

    try:
        sts = json.loads(sts_res.stdout)
    except json.JSONDecodeError:
        last_error = "failed to parse statefulset JSON"
        time.sleep(5)
        continue

    status = sts.get("status", {}) or {}
    ready = status.get("readyReplicas") or 0
    updated = status.get("updatedReplicas") or 0
    current_revision = status.get("currentRevision")
    update_revision = status.get("updateRevision")
    spec_replicas = ((sts.get("spec") or {}).get("replicas")) or 0

    pods_res = subprocess.run(
        ["kubectl", "-n", ns, "get", "pods", "-l", f"app={cluster}", "-o", "json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if pods_res.returncode != 0:
        last_error = pods_res.stderr.strip() or "failed to read pods"
        time.sleep(5)
        continue

    try:
        pods = json.loads(pods_res.stdout)
    except json.JSONDecodeError:
        last_error = "failed to parse pods JSON"
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

    last_error = (
        f"spec={spec_replicas} pods={len(items)} ready={ready} "
        f"updated={updated} pod_ready={pod_ready}"
    )
    time.sleep(5)

print(last_error, file=sys.stderr)
raise SystemExit(1)
PY
}

desired_members_js() {
  local members="" ordinal host
  for ((ordinal = 0; ordinal < target_replicas; ordinal++)); do
    host="${cluster}-${ordinal}.${service}.${ns}.svc.cluster.local:27017"
    [[ -z "${members}" ]] || members+=", "
    members+="{_id:${ordinal},host:\"${host}\"}"
  done
  printf '%s\n' "${members}"
}

force_reconcile_topology() {
  local members_js
  members_js="$(desired_members_js)"
  mongo_eval "${cluster}-0" "
    const desiredMembers = [${members_js}];
    try {
      const cfg = rs.conf();
      cfg.members = desiredMembers;
      cfg.version = (cfg.version || 1) + 1;
      rs.reconfig(cfg, {force: true});
    } catch (err) {
      if (!String(err).includes('no replset config has been received')) {
        throw err;
      }
      rs.initiate({_id: '${replica_set}', members: desiredMembers});
    }
  " >/dev/null
}

wait_topology_healthy() {
  local deadline primary_pod summary
  deadline=$((SECONDS + 360))
  while (( SECONDS < deadline )); do
    primary_pod="$(find_primary || true)"
    if [[ -n "${primary_pod}" ]]; then
      summary="$(
        mongo_eval "${primary_pod}" \
          "const members = rs.status().members || []; const primary = members.filter(m=>m.stateStr==='PRIMARY').length; const secondary = members.filter(m=>m.stateStr==='SECONDARY').length; print(JSON.stringify({members: members.length, primary, secondary}));" \
          2>/dev/null || true
      )"
      if python3 - "${summary}" "${target_replicas}" <<'PY'
from __future__ import annotations

import json
import sys

raw = sys.argv[1].strip()
expected = int(sys.argv[2])
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    raise SystemExit(1)

members = int(payload.get("members", 0))
primary = int(payload.get("primary", 0))
secondary = int(payload.get("secondary", 0))

if members == expected and primary == 1 and secondary == expected - 1:
    raise SystemExit(0)
raise SystemExit(1)
PY
      then
        return 0
      fi
    fi
    sleep 5
  done
  return 1
}

kubectl -n "${ns}" get statefulset "${cluster}" -o json > "${statefulset_json}"
patch_json="$(
  python3 - "${statefulset_json}" "${label_key}" "${label_value}" "${request_mi}" "${limit_mi}" \
    "${min_readiness_timeout}" "${min_readiness_failure_threshold}" \
    "${min_liveness_timeout}" "${min_liveness_failure_threshold}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
label_key = sys.argv[2]
label_value = sys.argv[3]
request_mi = sys.argv[4]
limit_mi = sys.argv[5]
min_readiness_timeout = int(sys.argv[6])
min_readiness_failure_threshold = int(sys.argv[7])
min_liveness_timeout = int(sys.argv[8])
min_liveness_failure_threshold = int(sys.argv[9])

container = ((((payload.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or [None])[0]
if not isinstance(container, dict):
    raise SystemExit("statefulset missing primary container")

ops: list[dict[str, object]] = [
    {"op": "replace", "path": f"/spec/template/metadata/labels/{label_key}", "value": label_value},
    {"op": "replace", "path": "/spec/template/spec/containers/0/resources/requests/memory", "value": f"{request_mi}Mi"},
    {"op": "replace", "path": "/spec/template/spec/containers/0/resources/limits/memory", "value": f"{limit_mi}Mi"},
]

def maybe_raise_probe_floor(probe_name: str, floor_timeout: int, floor_failure: int) -> None:
    probe = container.get(probe_name)
    if not isinstance(probe, dict):
        return

    timeout_value = probe.get("timeoutSeconds")
    timeout_op = "replace" if "timeoutSeconds" in probe else "add"
    if not isinstance(timeout_value, int) or timeout_value < floor_timeout:
        ops.append(
            {
                "op": timeout_op,
                "path": f"/spec/template/spec/containers/0/{probe_name}/timeoutSeconds",
                "value": floor_timeout,
            }
        )

    failure_value = probe.get("failureThreshold")
    failure_op = "replace" if "failureThreshold" in probe else "add"
    if not isinstance(failure_value, int) or failure_value < floor_failure:
        ops.append(
            {
                "op": failure_op,
                "path": f"/spec/template/spec/containers/0/{probe_name}/failureThreshold",
                "value": floor_failure,
            }
        )

maybe_raise_probe_floor("readinessProbe", min_readiness_timeout, min_readiness_failure_threshold)
maybe_raise_probe_floor("livenessProbe", min_liveness_timeout, min_liveness_failure_threshold)

print(json.dumps(ops))
PY
)"
kubectl -n "${ns}" patch statefulset "${cluster}" --type=json -p "${patch_json}" >/dev/null
kubectl -n "${ns}" scale statefulset "${cluster}" --replicas="${target_replicas}" >/dev/null

if ! kubectl -n "${ns}" rollout status "statefulset/${cluster}" --timeout=900s; then
  static_solver_log "rollout status reported a transient error; falling back to statefulset and pod readiness polling"
fi

wait_statefulset_ready

if ! wait_topology_healthy; then
  static_solver_log "MongoDB replica-set election did not recover after StatefulSet rollout; forcing topology reconciliation"
  force_reconcile_topology
  wait_topology_healthy || static_solver_fail "MongoDB replica set did not recover to a ${target_replicas}-member healthy topology"
fi

static_solver_write_submit "restored MongoDB StatefulSet readiness and replica-set topology"
