#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/master-downscale-voting-exclusions
# Strategy: native_shell
# Notes: Restore the original secured cluster topology if needed, then safely
# reduce the original StatefulSet to a single master while preserving any
# inherited non-master nodesets from earlier workflow stages.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
password_secret="${BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME:-elastic-password}"
password_key="${BENCH_PARAM_ELASTIC_PASSWORD_KEY:-password}"
original_replicas="${BENCH_PARAM_ORIGINAL_REPLICAS:-3}"
target_master_nodes="${BENCH_PARAM_TARGET_MASTER_NODES:-1}"

[[ "${target_master_nodes}" -ge 1 ]] || static_solver_fail "target_master_nodes must be at least 1"
[[ "${target_master_nodes}" -lt "${original_replicas}" ]] || static_solver_fail "target_master_nodes (${target_master_nodes}) must be lower than original_replicas (${original_replicas})"

elastic_password=""
if password_b64="$(kubectl -n "${ns}" get secret "${password_secret}" -o "jsonpath={.data.${password_key}}" 2>/dev/null)"; then
  if [[ -n "${password_b64}" ]]; then
    elastic_password="$(python3 - "${password_b64}" <<'PY'
import base64
import sys

value = sys.argv[1].strip()
print(base64.b64decode(value).decode() if value else "", end="")
PY
)"
  fi
fi

auth_args=()
if [[ -n "${elastic_password}" ]]; then
  auth_args=(-u "elastic:${elastic_password}")
fi

elastic_curl() {
  local max_time="${1:?max_time is required}"
  shift
  local cmd=(
    kubectl
    --request-timeout=40s
    -n
    "${ns}"
    exec
    "${curl_pod}"
    --
    curl
    -s
    -S
    -k
    --max-time
    "${max_time}"
  )
  if [[ ${#auth_args[@]} -gt 0 ]]; then
    cmd+=("${auth_args[@]}")
  fi
  cmd+=("$@")
  "${cmd[@]}"
}

probe_scheme() {
  local scheme="${1}"
  local code=""
  if ! code="$(elastic_curl 5 -o /dev/null -w '%{http_code}' "${scheme}://${service}:9200/" 2>/dev/null)"; then
    return 1
  fi
  [[ "${code}" =~ ^[0-9]+$ ]] && [[ "${code}" != "000" ]]
}

retry_elastic_curl() {
  local attempts="${1:?attempt count is required}"
  shift
  local description="${1:?description is required}"
  shift
  local delay_sec="${1:?delay is required}"
  shift
  local attempt

  for attempt in $(seq 1 "${attempts}"); do
    if elastic_curl "$@" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${delay_sec}"
  done

  static_solver_fail "${description}"
}

desired_total_nodes() {
  python3 - "${ns}" <<'PY'
import json
import subprocess
import sys

ns = sys.argv[1]
proc = subprocess.run(
    ["kubectl", "-n", ns, "get", "sts", "-o", "json"],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if proc.returncode != 0:
    raise SystemExit(1)

payload = json.loads(proc.stdout)
total = 0
for item in payload.get("items", []):
    metadata = item.get("metadata", {}) or {}
    if metadata.get("deletionTimestamp"):
        continue
    spec = item.get("spec", {}) or {}
    containers = (spec.get("template", {}) or {}).get("spec", {}).get("containers", []) or []
    if "elasticsearch" not in " ".join(container.get("image", "") for container in containers):
        continue
    replicas = spec.get("replicas")
    if isinstance(replicas, int) and replicas > 0:
        total += replicas

print(total, end="")
PY
}

extra_es_statefulsets() {
  python3 - "${ns}" "${prefix}" <<'PY'
import json
import subprocess
import sys

ns, original = sys.argv[1], sys.argv[2]
proc = subprocess.run(
    ["kubectl", "-n", ns, "get", "sts", "-o", "json"],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if proc.returncode != 0:
    raise SystemExit(1)

payload = json.loads(proc.stdout)
for item in payload.get("items", []):
    metadata = item.get("metadata", {}) or {}
    name = metadata.get("name")
    if not name or name == original or metadata.get("deletionTimestamp"):
        continue
    spec = item.get("spec", {}) or {}
    containers = (spec.get("template", {}) or {}).get("spec", {}).get("containers", []) or []
    if "elasticsearch" not in " ".join(container.get("image", "") for container in containers):
        continue
    replicas = spec.get("replicas")
    if isinstance(replicas, int) and replicas > 0:
        print(f"{name}\t{replicas}")
PY
}

wait_for_statefulset_pods_gone() {
  local name="${1:?statefulset name is required}"
  python3 - "${ns}" "${name}" <<'PY'
import json
import subprocess
import sys
import time

ns, target = sys.argv[1], sys.argv[2]
deadline = time.monotonic() + 600
while time.monotonic() < deadline:
    proc = subprocess.run(
        ["kubectl", "-n", ns, "get", "pods", "-o", "json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        time.sleep(5)
        continue
    payload = json.loads(proc.stdout)
    live = []
    for item in payload.get("items", []):
        for owner in item.get("metadata", {}).get("ownerReferences", []):
            if owner.get("kind") == "StatefulSet" and owner.get("name") == target:
                live.append(item.get("metadata", {}).get("name"))
                break
    if not live:
        raise SystemExit(0)
    time.sleep(5)
raise SystemExit(1)
PY
}

wait_for_cluster_health() {
  local expected_nodes="${1:?expected node count is required}"
  local deadline=$((SECONDS + 300))
  while (( SECONDS < deadline )); do
    if elastic_curl 20 "${scheme}://${service}:9200/_cluster/health?wait_for_status=yellow&wait_for_nodes=${expected_nodes}&wait_for_no_relocating_shards=true&timeout=10s" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  static_solver_fail "cluster did not stabilize at ${expected_nodes} nodes"
}

scheme="https"
if ! probe_scheme "${scheme}"; then
  scheme="http"
  probe_scheme "${scheme}" || static_solver_fail "failed to detect a live Elasticsearch HTTP scheme for ${service}"
fi

kubectl -n "${ns}" scale "statefulset/${prefix}" --replicas="${original_replicas}" >/dev/null
for ordinal in $(seq 0 $((original_replicas - 1))); do
  kubectl -n "${ns}" wait --for=condition=ready "pod/${prefix}-${ordinal}" --timeout=900s >/dev/null
done

wait_for_cluster_health "$(desired_total_nodes)"

retry_elastic_curl 20 "failed to enable cluster.auto_shrink_voting_configuration" 3 \
  20 -XPUT "${scheme}://${service}:9200/_cluster/settings" \
  -H 'Content-Type: application/json' \
  -d '{"persistent":{"cluster.auto_shrink_voting_configuration":true}}'

elastic_curl 20 -XDELETE "${scheme}://${service}:9200/_cluster/voting_config_exclusions?wait_for_removal=false" >/dev/null 2>&1 || true

retry_elastic_curl 20 "failed to pin allocation onto ${prefix}-0 before downscale" 3 \
  20 -XPUT "${scheme}://${service}:9200/_cluster/settings" \
  -H 'Content-Type: application/json' \
  -d "{\"transient\":{\"cluster.routing.allocation.require._name\":\"${prefix}-0\"}}"

wait_for_cluster_health "$(desired_total_nodes)"

while IFS=$'\t' read -r extra_sts extra_replicas; do
  [[ -n "${extra_sts}" ]] || continue
  static_solver_log "scaling inherited statefulset/${extra_sts} to 0 replicas"
  kubectl -n "${ns}" scale "statefulset/${extra_sts}" --replicas=0 >/dev/null
  wait_for_statefulset_pods_gone "${extra_sts}" || static_solver_fail "timed out waiting for statefulset/${extra_sts} pods to terminate"
done < <(extra_es_statefulsets)

wait_for_cluster_health "$(desired_total_nodes)"

if [[ "${original_replicas}" -gt "${target_master_nodes}" ]]; then
  exclusions=()
  for ordinal in $(seq "${target_master_nodes}" $((original_replicas - 1))); do
    exclusions+=("${prefix}-${ordinal}")
  done
  exclusions_csv="$(IFS=,; printf '%s' "${exclusions[*]}")"
  retry_elastic_curl 20 "failed to add voting exclusions for ${exclusions_csv}" 3 \
    20 -XPOST "${scheme}://${service}:9200/_cluster/voting_config_exclusions?node_names=${exclusions_csv}&timeout=60s"
fi

kubectl -n "${ns}" scale "statefulset/${prefix}" --replicas="${target_master_nodes}" >/dev/null
for ordinal in $(seq "${target_master_nodes}" $((original_replicas - 1))); do
  kubectl -n "${ns}" wait --for=delete "pod/${prefix}-${ordinal}" --timeout=600s >/dev/null
done

retry_elastic_curl 20 "failed to clear voting exclusions after downscale" 3 \
  20 -XDELETE "${scheme}://${service}:9200/_cluster/voting_config_exclusions?wait_for_removal=true"

retry_elastic_curl 20 "failed to clear temporary allocation pin after downscale" 3 \
  20 -XPUT "${scheme}://${service}:9200/_cluster/settings" \
  -H 'Content-Type: application/json' \
  -d '{"transient":{"cluster.routing.allocation.require._name":null}}'

wait_for_cluster_health "$(desired_total_nodes)"

static_solver_write_submit "recovered voting configuration and safely downscaled"
