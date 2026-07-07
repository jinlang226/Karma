#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/version-upgrade-hard
# Strategy: native_shell
# Notes: Perform the supported multi-hop 5.0 -> 6.0.5 -> 7.0.5 upgrade while
# preserving chained TLS/auth state and any workflow-expanded replica count.

static_solver_export_namespace_if_unset "mongodb"

ns="${BENCH_NAMESPACE}"
cluster="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
admin_secret="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
admin_user="${BENCH_PARAM_ADMIN_USERNAME:-admin-user}"
intermediate_image="${BENCH_PARAM_INTERMEDIATE_IMAGE:-mongo:6.0.5}"
intermediate_version_prefix="${BENCH_PARAM_INTERMEDIATE_VERSION_PREFIX:-6.0}"
intermediate_fcv="${BENCH_PARAM_INTERMEDIATE_FCV:-6.0}"
to_image="${BENCH_PARAM_TO_IMAGE:-mongo:7.0.5}"
to_version_prefix="${BENCH_PARAM_TO_VERSION_PREFIX:-7.0}"
to_fcv="${BENCH_PARAM_TO_FCV:-7.0}"

expected_replicas="$(
  kubectl -n "${ns}" get statefulset "${cluster}" -o jsonpath='{.spec.replicas}' 2>/dev/null || printf '3'
)"
[[ "${expected_replicas}" =~ ^[0-9]+$ ]] || static_solver_fail "expected replica count must be numeric"
(( expected_replicas > 0 )) || static_solver_fail "expected replica count must be positive"

admin_password="$(
  kubectl -n "${ns}" get secret "${admin_secret}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
)"
[[ -n "${admin_password}" ]] || static_solver_fail "unable to read admin password from secret ${admin_secret}"

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
  for ((ordinal = 0; ordinal < expected_replicas; ordinal++)); do
    pod="${cluster}-${ordinal}"
    if mongo_eval "${pod}" 'db.hello().isWritablePrimary' 2>/dev/null | grep -q true; then
      printf '%s\n' "${pod}"
      return 0
    fi
  done
  return 1
}

read_db_version() {
  local primary_pod="${1:?primary pod is required}"
  mongo_eval "${primary_pod}" 'db.version()' 2>/dev/null | tail -n 1 | tr -d '"[:space:]\r'
}

read_fcv() {
  local primary_pod="${1:?primary pod is required}"
  mongo_eval "${primary_pod}" \
    'db.adminCommand({getParameter:1,featureCompatibilityVersion:1}).featureCompatibilityVersion.version' \
    2>/dev/null | tail -n 1 | tr -d '"[:space:]\r'
}

wait_statefulset_image() {
  local image="${1:?image is required}"
  python3 - "${ns}" "${cluster}" "${expected_replicas}" "${image}" <<'PY'
from __future__ import annotations

import json
import subprocess
import sys
import time

ns, cluster, expected, image = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
deadline = time.monotonic() + 900
last_error = f"timed out waiting for {cluster} to reach image {image}"

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

    spec = sts.get("spec", {}) or {}
    status = sts.get("status", {}) or {}
    containers = (((spec.get("template") or {}).get("spec") or {}).get("containers")) or []
    template_image = containers[0].get("image") if containers else None
    spec_replicas = spec.get("replicas") or 0
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
        last_error = pods_res.stderr.strip() or "failed to read pods"
        time.sleep(5)
        continue

    try:
        pods = json.loads(pods_res.stdout)
    except json.JSONDecodeError:
        last_error = "failed to parse pod list JSON"
        time.sleep(5)
        continue

    items = pods.get("items", [])
    pod_ready = 0
    pod_images_match = True
    for item in items:
        conditions = (item.get("status", {}) or {}).get("conditions", []) or []
        if any(cond.get("type") == "Ready" and cond.get("status") == "True" for cond in conditions):
            pod_ready += 1
        pod_containers = (item.get("spec", {}) or {}).get("containers") or []
        pod_image = pod_containers[0].get("image") if pod_containers else None
        if pod_image != image:
            pod_images_match = False

    if (
        spec_replicas == expected
        and ready == expected
        and updated == expected
        and len(items) == expected
        and pod_ready == expected
        and template_image == image
        and pod_images_match
        and current_revision
        and current_revision == update_revision
    ):
        raise SystemExit(0)

    last_error = (
        f"spec={spec_replicas} ready={ready} updated={updated} "
        f"pods={len(items)} pod_ready={pod_ready} template_image={template_image}"
    )
    time.sleep(5)

print(last_error, file=sys.stderr)
raise SystemExit(1)
PY
}

wait_topology_healthy() {
  local deadline primary_pod summary
  deadline=$((SECONDS + 480))
  while (( SECONDS < deadline )); do
    primary_pod="$(find_primary || true)"
    if [[ -z "${primary_pod}" ]]; then
      sleep 5
      continue
    fi
    summary="$(
      mongo_eval "${primary_pod}" \
        "const members = rs.status().members || []; const primary = members.filter(m=>m.stateStr==='PRIMARY').length; const secondary = members.filter(m=>m.stateStr==='SECONDARY').length; print(JSON.stringify({members: members.length, primary, secondary}));" \
        2>/dev/null || true
    )"
    if python3 - "${summary}" "${expected_replicas}" <<'PY'
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
    sleep 5
  done
  return 1
}

wait_version_prefix() {
  local target_prefix="${1:?target prefix is required}"
  local deadline primary_pod version
  deadline=$((SECONDS + 240))
  while (( SECONDS < deadline )); do
    primary_pod="$(find_primary || true)"
    if [[ -z "${primary_pod}" ]]; then
      sleep 5
      continue
    fi
    version="$(read_db_version "${primary_pod}" || true)"
    if [[ "${version}" == "${target_prefix}"* ]]; then
      return 0
    fi
    sleep 5
  done
  return 1
}

set_fcv() {
  local target_fcv="${1:?target FCV is required}"
  local deadline primary_pod current_fcv
  deadline=$((SECONDS + 300))
  while (( SECONDS < deadline )); do
    primary_pod="$(find_primary || true)"
    if [[ -z "${primary_pod}" ]]; then
      sleep 5
      continue
    fi

    current_fcv="$(read_fcv "${primary_pod}" || true)"
    if [[ "${current_fcv}" == "${target_fcv}" ]]; then
      return 0
    fi

    if ! mongo_eval "${primary_pod}" "db.adminCommand({setFeatureCompatibilityVersion:\"${target_fcv}\"})" >/dev/null 2>&1; then
      mongo_eval "${primary_pod}" "db.adminCommand({setFeatureCompatibilityVersion:\"${target_fcv}\",confirm:true})" >/dev/null 2>&1 || true
    fi

    sleep 5
  done
  return 1
}

rollout_image() {
  local image="${1:?image is required}"
  local version_prefix="${2:?version prefix is required}"

  kubectl -n "${ns}" set image "statefulset/${cluster}" "mongod=${image}" >/dev/null
  if ! kubectl -n "${ns}" rollout status "statefulset/${cluster}" --timeout=900s; then
    static_solver_log "rollout status reported a transient error during MongoDB upgrade; falling back to readiness polling"
  fi

  wait_statefulset_image "${image}"
  wait_topology_healthy || static_solver_fail "MongoDB topology did not recover after upgrading to ${image}"
  wait_version_prefix "${version_prefix}" || static_solver_fail "MongoDB primary never reported version prefix ${version_prefix}"
}

primary_pod=""
for _ in $(seq 1 60); do
  primary_pod="$(find_primary || true)"
  if [[ -n "${primary_pod}" ]]; then
    break
  fi
  sleep 3
done
[[ -n "${primary_pod}" ]] || static_solver_fail "unable to locate MongoDB primary before version upgrade"

current_version="$(read_db_version "${primary_pod}")"

if [[ "${current_version}" != "${intermediate_version_prefix}"* && "${current_version}" != "${to_version_prefix}"* ]]; then
  rollout_image "${intermediate_image}" "${intermediate_version_prefix}"
  set_fcv "${intermediate_fcv}" || static_solver_fail "failed to advance FCV to ${intermediate_fcv}"
fi

current_version="$(read_db_version "$(find_primary)")"
if [[ "${current_version}" != "${to_version_prefix}"* ]]; then
  rollout_image "${to_image}" "${to_version_prefix}"
fi

set_fcv "${to_fcv}" || static_solver_fail "failed to advance FCV to ${to_fcv}"
wait_statefulset_image "${to_image}"
wait_topology_healthy || static_solver_fail "MongoDB topology did not recover after final upgrade"
wait_version_prefix "${to_version_prefix}" || static_solver_fail "MongoDB primary never reported final version prefix ${to_version_prefix}"

static_solver_write_submit "upgraded MongoDB to ${to_image} and finalized FCV ${to_fcv}"
