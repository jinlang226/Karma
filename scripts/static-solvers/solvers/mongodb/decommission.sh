#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/decommission
# Strategy: native_shell
# Notes: Remove the requested member from the live replica-set config, then wait
# for the StatefulSet scale-down and post-removal election to settle before
# submitting so the workload check does not race the controller.

static_solver_export_namespace_if_unset "mongodb"

ns="${BENCH_NAMESPACE}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongo-rs}"
service="${BENCH_PARAM_SERVICE_NAME:-mongo}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
target_replicas="${BENCH_PARAM_TARGET_REPLICAS:-2}"
removed_member_index="${BENCH_PARAM_REMOVED_MEMBER_INDEX:-2}"

[[ "${target_replicas}" =~ ^[0-9]+$ ]] || static_solver_fail "target replica count must be numeric"
[[ "${removed_member_index}" =~ ^[0-9]+$ ]] || static_solver_fail "removed member index must be numeric"
(( target_replicas > 0 )) || static_solver_fail "target replica count must be positive"

search_replicas="$(
  kubectl -n "${ns}" get statefulset "${cluster}" -o jsonpath='{.spec.replicas}' 2>/dev/null || printf '%s' "$((target_replicas + 1))"
)"

admin_password="$(
  kubectl -n "${ns}" get secret "${admin_secret}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
)"

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

mongo_uri="mongodb://localhost:27017/admin?directConnection=true"
if [[ -n "${admin_password}" ]]; then
  mongo_uri="mongodb://${admin_user}:${admin_password}@localhost:27017/admin?directConnection=true"
fi

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
  for ((ordinal = 0; ordinal < search_replicas; ordinal++)); do
    pod="${cluster}-${ordinal}"
    if mongo_eval "${pod}" 'db.hello().isWritablePrimary' 2>/dev/null | grep -q true; then
      printf '%s\n' "${pod}"
      return 0
    fi
  done
  return 1
}

wait_statefulset_scaled_down() {
  python3 - "${ns}" "${cluster}" "${target_replicas}" "${removed_member_index}" <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
import time

ns, cluster, expected, removed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
removed_pod = f"{cluster}-{removed}"
deadline = time.monotonic() + 600
last_error = f"timed out waiting for {cluster} to scale to {expected} ready replicas"

while time.monotonic() < deadline:
    sts_res = subprocess.run(
        ["kubectl", "-n", ns, "get", "statefulset", cluster, "-o", "json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if sts_res.returncode != 0:
        last_error = sts_res.stderr.strip() or "failed to read statefulset"
        time.sleep(3)
        continue

    try:
        sts = json.loads(sts_res.stdout)
    except json.JSONDecodeError:
        last_error = "failed to parse statefulset JSON"
        time.sleep(3)
        continue

    spec = (sts.get("spec") or {}).get("replicas") or 0
    ready = (sts.get("status") or {}).get("readyReplicas") or 0

    pods_res = subprocess.run(
        ["kubectl", "-n", ns, "get", "pods", "-l", f"app={cluster}", "-o", "json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if pods_res.returncode != 0:
        last_error = pods_res.stderr.strip() or "failed to read pods"
        time.sleep(3)
        continue

    try:
        pods = json.loads(pods_res.stdout)
    except json.JSONDecodeError:
        last_error = "failed to parse pods JSON"
        time.sleep(3)
        continue

    items = pods.get("items", [])
    names = {item.get("metadata", {}).get("name", "") for item in items}
    pod_ready = 0
    for item in items:
        conditions = (item.get("status", {}) or {}).get("conditions", []) or []
        if any(cond.get("type") == "Ready" and cond.get("status") == "True" for cond in conditions):
            pod_ready += 1

    if spec == expected and ready == expected and len(items) == expected and pod_ready == expected and removed_pod not in names:
        raise SystemExit(0)

    last_error = (
        f"spec={spec} ready={ready} pods={len(items)} pod_ready={pod_ready} "
        f"removed_present={removed_pod in names}"
    )
    time.sleep(3)

print(last_error, file=sys.stderr)
raise SystemExit(1)
PY
}

wait_topology_healthy() {
  local removed_host primary_pod summary
  removed_host="${cluster}-${removed_member_index}.${service}.${ns}.svc.cluster.local:27017"
  search_replicas="${target_replicas}"
  for _ in $(seq 1 120); do
    primary_pod="$(find_primary || true)"
    if [[ -z "${primary_pod}" ]]; then
      sleep 3
      continue
    fi
    summary="$(
      mongo_eval "${primary_pod}" "
        const conf = rs.conf();
        const status = rs.status();
        const hosts = conf.members.map(m => m.host);
        const primary = status.members.filter(m => m.stateStr === 'PRIMARY').length;
        const secondary = status.members.filter(m => m.stateStr === 'SECONDARY').length;
        print(JSON.stringify({
          members: conf.members.length,
          hasRemoved: hosts.includes('${removed_host}'),
          primary,
          secondary
        }));
      " 2>/dev/null || true
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
has_removed = bool(payload.get("hasRemoved"))
primary = int(payload.get("primary", 0))
secondary = int(payload.get("secondary", 0))

if members == expected and not has_removed and primary == 1 and secondary == expected - 1:
    raise SystemExit(0)
raise SystemExit(1)
PY
    then
      return 0
    fi
    sleep 3
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
[[ -n "${primary_pod}" ]] || static_solver_fail "unable to locate MongoDB primary before decommission"

removed_host="${cluster}-${removed_member_index}.${service}.${ns}.svc.cluster.local:27017"
member_present="$(
  mongo_eval "${primary_pod}" "rs.conf().members.some(m => m.host === '${removed_host}')" 2>/dev/null || true
)"
if [[ "${member_present}" == "true" ]]; then
  mongo_eval "${primary_pod}" "rs.remove('${removed_host}')" >/dev/null
fi

kubectl -n "${ns}" scale "statefulset/${cluster}" --replicas="${target_replicas}" >/dev/null
kubectl -n "${ns}" rollout status "statefulset/${cluster}" --timeout=600s >/dev/null || true

wait_statefulset_scaled_down
wait_topology_healthy || static_solver_fail "MongoDB replica set did not settle to the expected post-decommission topology"

static_solver_write_submit "decommissioned MongoDB member and confirmed stable ${target_replicas}-member replica set"
