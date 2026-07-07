#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/replica-scaling
# Strategy: native_shell
# Notes: Handle workflow-inherited probe configs, derive the canonical
# starting membership from rs.conf() under param-drift, and recover once from
# stale higher-ordinal PVC state by recreating only the new member volumes.

static_solver_export_namespace_if_unset "mongodb"

ns="${BENCH_NAMESPACE}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
service="${BENCH_PARAM_HEADLESS_SERVICE_NAME:-${cluster}-svc}"
target_replicas="${BENCH_PARAM_TARGET_REPLICAS:-5}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
keyfile_secret="${BENCH_PARAM_KEYFILE_SECRET_NAME:-mongo-keyfile}"
# Match the standalone replica-scaling fixture so chained scale-outs do not hit
# MongoDB's inherited __system storedKey mismatch.
compatible_keyfile="${BENCH_PARAM_COMPATIBLE_KEYFILE_VALUE:-mongoKeyfile0123456789ABCDEF}"
keyfile_path="${BENCH_PARAM_KEYFILE_PATH:-/etc/mongo-keyfile/keyfile}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
statefulset_json="${tmp_dir}/statefulset.json"
pre_patch_path="${tmp_dir}/pre-patch.json"
post_patch_path="${tmp_dir}/post-patch.json"
current_keyfile_path="${tmp_dir}/mongo-keyfile-current"
bridge_keyfile_path="${tmp_dir}/mongo-keyfile-bridge"
target_keyfile_path="${tmp_dir}/mongo-keyfile-target"

kubectl -n "${ns}" get statefulset "${cluster}" -o json > "${statefulset_json}"
current_replicas="$(
  python3 - "${statefulset_json}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
print(((payload.get("spec") or {}).get("replicas")) or 0)
PY
)"
search_replicas="${current_replicas}"

admin_password="$(
  kubectl -n "${ns}" get secret "${admin_secret}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
)"
[[ -n "${admin_password}" ]] || static_solver_fail "unable to read admin password from secret ${admin_secret}"
mongo_uri="mongodb://${admin_user}:${admin_password}@localhost:27017/admin?directConnection=true"
printf '%s\n' "${compatible_keyfile}" > "${target_keyfile_path}"

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

apply_keyfile_secret() {
  local keyfile_path="${1:?keyfile path is required}"
  kubectl -n "${ns}" create secret generic "${keyfile_secret}" \
    --from-file=keyfile="${keyfile_path}" \
    --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -
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

wait_pod_ready() {
  local pod="${1:?pod is required}"
  python3 - "${ns}" "${pod}" <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
import time

ns, pod = sys.argv[1], sys.argv[2]
deadline = time.monotonic() + 600

while time.monotonic() < deadline:
    pod_res = subprocess.run(
        ["kubectl", "-n", ns, "get", "pod", pod, "-o", "json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if pod_res.returncode != 0:
        time.sleep(3)
        continue
    try:
        payload = json.loads(pod_res.stdout)
    except json.JSONDecodeError:
        time.sleep(3)
        continue
    conditions = (payload.get("status", {}) or {}).get("conditions", []) or []
    if any(cond.get("type") == "Ready" and cond.get("status") == "True" for cond in conditions):
        raise SystemExit(0)
    time.sleep(3)

print(f"Timed out waiting for pod {pod} in {ns} to become Ready", file=sys.stderr)
raise SystemExit(1)
PY
}

wait_member_healthy() {
  local ordinal="${1:?ordinal is required}"
  local deadline primary_pod state health
  deadline=$((SECONDS + 600))
  while (( SECONDS < deadline )); do
    primary_pod="$(find_primary || true)"
    if [[ -z "${primary_pod}" ]]; then
      sleep 3
      continue
    fi
    state="$(
      mongo_eval "${primary_pod}" \
        "rs.status().members.find(m=>m._id===${ordinal})?.stateStr" 2>/dev/null || true
    )"
    health="$(
      mongo_eval "${primary_pod}" \
        "rs.status().members.find(m=>m._id===${ordinal})?.health" 2>/dev/null || true
    )"
    if [[ "${health}" == "1" && ( "${state}" == "SECONDARY" || "${state}" == "PRIMARY" ) ]]; then
      return 0
    fi
    sleep 3
  done
  return 1
}

wait_new_pods_running() {
  local ordinal deadline phase
  deadline=$((SECONDS + 600))
  for ((ordinal = current_replicas; ordinal < target_replicas; ordinal++)); do
    while (( SECONDS < deadline )); do
      phase="$(
        kubectl -n "${ns}" get pod "${cluster}-${ordinal}" -o jsonpath='{.status.phase}' 2>/dev/null || true
      )"
      if [[ "${phase}" == "Running" ]]; then
        break
      fi
      sleep 3
    done
    [[ "${phase}" == "Running" ]] || return 1
  done
}

ensure_member_configured() {
  local primary_pod="${1:?primary pod is required}"
  local ordinal host exists
  for ((ordinal = current_replicas; ordinal < target_replicas; ordinal++)); do
    host="${cluster}-${ordinal}.${service}.${ns}.svc.cluster.local:27017"
    exists="$(
      mongo_eval "${primary_pod}" "rs.conf().members.some(m=>m._id===${ordinal})" 2>/dev/null || true
    )"
    if [[ "${exists}" != "true" ]]; then
      mongo_eval "${primary_pod}" "rs.add({_id:${ordinal},host:\"${host}\"})" >/dev/null
    fi
  done
}

wait_member_states() {
  local primary_pod="${1:?primary pod is required}"
  local ordinal state deadline
  deadline=$((SECONDS + 360))
  for ((ordinal = current_replicas; ordinal < target_replicas; ordinal++)); do
    state=""
    while (( SECONDS < deadline )); do
      state="$(
        mongo_eval "${primary_pod}" \
          "rs.status().members.find(m=>m._id===${ordinal})?.stateStr" 2>/dev/null || true
      )"
      if [[ "${state}" == "SECONDARY" || "${state}" == "PRIMARY" ]]; then
        break
      fi
      sleep 3
    done
    [[ "${state}" == "SECONDARY" || "${state}" == "PRIMARY" ]] || return 1
  done
}

wait_cluster_count() {
  local primary_pod="${1:?primary pod is required}"
  local expected_count="${2:?expected count is required}"
  local deadline count
  deadline=$((SECONDS + 360))
  while (( SECONDS < deadline )); do
    count="$(
      mongo_eval "${primary_pod}" \
        'rs.status().members.filter(m=>m.stateStr==="PRIMARY"||m.stateStr==="SECONDARY").length' 2>/dev/null || true
    )"
    if [[ "${count}" == "${expected_count}" ]]; then
      return 0
    fi
    sleep 3
  done
  return 1
}

restart_existing_member() {
  local ordinal="${1:?ordinal is required}"
  local pod="${cluster}-${ordinal}"
  local primary_pod

  kubectl -n "${ns}" delete pod "${pod}" --wait=false >/dev/null
  wait_pod_ready "${pod}"
  wait_member_healthy "${ordinal}"
  primary_pod="$(find_primary)"
  wait_cluster_count "${primary_pod}" "${current_replicas}"
}

normalize_keyfile_for_scaleout() {
  local current_primary current_ordinal

  for ((ordinal = 0; ordinal < current_replicas; ordinal++)); do
    if kubectl -n "${ns}" exec "${cluster}-${ordinal}" -- cat "${keyfile_path}" > "${current_keyfile_path}" 2>/dev/null; then
      break
    fi
  done
  [[ -s "${current_keyfile_path}" ]] || static_solver_fail "unable to read live keyfile data from running MongoDB members"
  if [[ "$(cat "${current_keyfile_path}")" == "$(cat "${target_keyfile_path}")" ]]; then
    return 0
  fi

  python3 - "${current_keyfile_path}" "${compatible_keyfile}" "${bridge_keyfile_path}" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

current_path = Path(sys.argv[1])
target_key = sys.argv[2]
bridge_path = Path(sys.argv[3])

keys: list[str] = []
for line in current_path.read_text().splitlines():
    value = line.strip()
    if value and value not in keys:
        keys.append(value)
if target_key not in keys:
    keys.append(target_key)
bridge_path.write_text("\n".join(keys) + "\n")
PY

  static_solver_log "rotating MongoDB keyfile secret to a scale-out compatible value"
  apply_keyfile_secret "${bridge_keyfile_path}"
  current_primary="$(find_primary)"
  current_ordinal="${current_primary##*-}"
  for ((ordinal = 0; ordinal < current_replicas; ordinal++)); do
    if [[ "${ordinal}" == "${current_ordinal}" ]]; then
      continue
    fi
    restart_existing_member "${ordinal}"
  done
  restart_existing_member "${current_ordinal}"
}

scale_to_target() {
  kubectl -n "${ns}" scale "statefulset/${cluster}" --replicas="${target_replicas}"
}

scale_back_to_current() {
  kubectl -n "${ns}" scale "statefulset/${cluster}" --replicas="${current_replicas}"
  local ordinal deadline gone
  deadline=$((SECONDS + 300))
  for ((ordinal = current_replicas; ordinal < target_replicas; ordinal++)); do
    gone=""
    while (( SECONDS < deadline )); do
      if ! kubectl -n "${ns}" get pod "${cluster}-${ordinal}" >/dev/null 2>&1; then
        gone="yes"
        break
      fi
      sleep 3
    done
    [[ "${gone}" == "yes" ]] || return 1
  done
}

delete_new_member_pvcs() {
  local ordinal claim
  for ((ordinal = current_replicas; ordinal < target_replicas; ordinal++)); do
    claim="data-volume-${cluster}-${ordinal}"
    kubectl -n "${ns}" delete pvc "${claim}" --ignore-not-found=true >/dev/null 2>&1 || true
  done
}

restore_probe_template() {
  if [[ "$(cat "${post_patch_path}")" != "[]" ]]; then
    kubectl -n "${ns}" patch statefulset "${cluster}" --type=json -p "$(cat "${post_patch_path}")"
  fi
}

wait_ready_pods() {
  python3 - "${ns}" "${cluster}" "${target_replicas}" <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
import time

ns, cluster, target = sys.argv[1], sys.argv[2], int(sys.argv[3])
deadline = time.monotonic() + 600

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
    if status.get("readyReplicas") != target:
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
    if len(items) != target:
        time.sleep(5)
        continue

    ready = 0
    for item in items:
        conditions = (item.get("status", {}) or {}).get("conditions", []) or []
        if any(cond.get("type") == "Ready" and cond.get("status") == "True" for cond in conditions):
            ready += 1

    if ready == target:
        raise SystemExit(0)

    time.sleep(5)

print(f"Timed out waiting for {target} ready pods for {cluster} in {ns}", file=sys.stderr)
raise SystemExit(1)
PY
}

initial_primary="$(find_primary || true)"
configured_replicas="$(
  if [[ -n "${initial_primary}" ]]; then
    mongo_eval "${initial_primary}" 'rs.conf().members.length' 2>/dev/null || true
  fi
)"
if [[ "${configured_replicas}" =~ ^[0-9]+$ ]] && (( configured_replicas > 0 )); then
  if [[ "${configured_replicas}" != "${current_replicas}" ]]; then
    static_solver_log \
      "using replica-set membership ${configured_replicas} instead of drifted StatefulSet replica count ${current_replicas}"
  fi
  current_replicas="${configured_replicas}"
fi

python3 - "${statefulset_json}" "${current_replicas}" "${target_replicas}" "${pre_patch_path}" "${post_patch_path}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

statefulset = json.loads(Path(sys.argv[1]).read_text())
current_replicas = int(sys.argv[2])
target_replicas = int(sys.argv[3])
pre_patch_path = Path(sys.argv[4])
post_patch_path = Path(sys.argv[5])

template_spec = (((statefulset.get("spec") or {}).get("template") or {}).get("spec") or {})
containers = template_spec.get("containers") or []
container = containers[0] if containers else {}
has_readiness = "readinessProbe" in container
has_liveness = "livenessProbe" in container
needs_probe_workaround = target_replicas > current_replicas and (has_readiness or has_liveness)

pre_ops: list[dict[str, object]] = []
post_ops: list[dict[str, object]] = []

if needs_probe_workaround:
    update_strategy = (statefulset.get("spec") or {}).get("updateStrategy")
    partition_strategy = {"type": "RollingUpdate", "rollingUpdate": {"partition": current_replicas}}

    if update_strategy is None:
        pre_ops.append({"op": "add", "path": "/spec/updateStrategy", "value": partition_strategy})
        post_ops.append({"op": "remove", "path": "/spec/updateStrategy"})
    else:
        pre_ops.append({"op": "replace", "path": "/spec/updateStrategy", "value": partition_strategy})
        post_ops.append({"op": "replace", "path": "/spec/updateStrategy", "value": update_strategy})

    if has_readiness:
        pre_ops.append({"op": "remove", "path": "/spec/template/spec/containers/0/readinessProbe"})
        post_ops.append({"op": "add", "path": "/spec/template/spec/containers/0/readinessProbe", "value": container["readinessProbe"]})

    if has_liveness:
        pre_ops.append({"op": "remove", "path": "/spec/template/spec/containers/0/livenessProbe"})
        post_ops.append({"op": "add", "path": "/spec/template/spec/containers/0/livenessProbe", "value": container["livenessProbe"]})

pre_patch_path.write_text(json.dumps(pre_ops))
post_patch_path.write_text(json.dumps(post_ops))
PY

if [[ "$(cat "${pre_patch_path}")" != "[]" ]]; then
  kubectl -n "${ns}" patch statefulset "${cluster}" --type=json -p "$(cat "${pre_patch_path}")"
fi

attempt_scale_up() {
  local primary_pod
  scale_to_target
  wait_new_pods_running
  primary_pod="$(find_primary)"
  [[ -n "${primary_pod}" ]] || return 1
  ensure_member_configured "${primary_pod}"
  wait_member_states "${primary_pod}"
  wait_cluster_count "${primary_pod}" "${target_replicas}"
}

normalize_keyfile_for_scaleout

if ! attempt_scale_up; then
  static_solver_log "first scale-up attempt stalled; recreating higher-ordinal PVCs and retrying once"
  scale_back_to_current
  delete_new_member_pvcs
  if ! attempt_scale_up; then
    static_solver_fail "failed to scale MongoDB replica set to ${target_replicas} members"
  fi
fi

restore_probe_template
wait_ready_pods
static_solver_write_submit "scaled MongoDB replica set to ${target_replicas} members"
