#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/bootstrap-initial-master-nodes
# Strategy: native_shell
# Notes: The case starts with cluster.initial_master_nodes missing, so the cluster
# cannot elect a master. Add the bootstrap-only setting long enough for the first
# cluster formation, then remove it and prove a rolling restart stays healthy.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
expected_nodes="${BENCH_PARAM_EXPECTED_NODES:-3}"

[[ "${expected_nodes}" =~ ^[0-9]+$ ]] || static_solver_fail "expected node count must be numeric"
(( expected_nodes > 0 )) || static_solver_fail "expected node count must be positive"

patch_bootstrap_config() {
  local mode="${1:?mode is required}"

  python3 - "${ns}" "${mode}" "${expected_nodes}" "${prefix}" <<'PY' | kubectl -n "${ns}" apply -f -
import json
import subprocess
import sys

ns, mode, expected_nodes, prefix = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
result = subprocess.run(
    ["kubectl", "-n", ns, "get", "configmap", "es-config", "-o", "json"],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if result.returncode != 0:
    raise SystemExit(result.stderr.strip() or "failed to read es-config")

obj = json.loads(result.stdout)
text = obj["data"]["elasticsearch.yml"]
lines = text.splitlines()
updated = []
i = 0
while i < len(lines):
    if lines[i].strip() == "cluster.initial_master_nodes:":
        i += 1
        while i < len(lines) and lines[i].startswith("  - "):
            i += 1
        continue
    updated.append(lines[i])
    i += 1

if mode == "add":
    if updated and updated[-1].strip():
        updated.append("")
    updated.append("cluster.initial_master_nodes:")
    for ordinal in range(expected_nodes):
        updated.append(f"  - {prefix}-{ordinal}")
elif mode != "remove":
    raise SystemExit(f"unsupported mode: {mode}")

obj["data"]["elasticsearch.yml"] = "\n".join(updated).rstrip() + "\n"
for key in ("managedFields", "resourceVersion", "uid", "creationTimestamp"):
    obj.get("metadata", {}).pop(key, None)
obj.pop("status", None)
print(json.dumps(obj))
PY
}

assert_bootstrap_presence() {
  local expected="${1:?expected state is required}"
  local config_text=""

  config_text="$(
    kubectl -n "${ns}" get configmap es-config -o jsonpath='{.data.elasticsearch\.yml}'
  )"

  if [[ "${expected}" == "present" ]]; then
    grep -q 'cluster\.initial_master_nodes' <<< "${config_text}" ||
      static_solver_fail "cluster.initial_master_nodes was not written to es-config"
  else
    if grep -q 'cluster\.initial_master_nodes' <<< "${config_text}"; then
      static_solver_fail "cluster.initial_master_nodes is still present in es-config"
    fi
  fi
}

restart_and_wait() {
  kubectl -n "${ns}" rollout restart "statefulset/${prefix}" >/dev/null

  local target_revision=""
  local revision_deadline=$((SECONDS + 60))
  while (( SECONDS < revision_deadline )); do
    target_revision="$(
      kubectl -n "${ns}" get "statefulset/${prefix}" -o jsonpath='{.status.updateRevision}' 2>/dev/null || true
    )"
    if [[ -n "${target_revision}" ]]; then
      break
    fi
    sleep 2
  done

  [[ -n "${target_revision}" ]] || static_solver_fail "statefulset/${prefix} did not report an update revision"

  python3 - "${ns}" "${prefix}" "${expected_nodes}" "${target_revision}" <<'PY'
import json
import subprocess
import sys
import time

ns, prefix, expected_nodes, target_revision = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
deadline = time.monotonic() + 900
last_error = ""

while time.monotonic() < deadline:
    result = subprocess.run(
        ["kubectl", "-n", ns, "get", "pods", "-l", f"app={prefix}", "-o", "json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        last_error = result.stderr.strip() or "failed to read Elasticsearch pods"
        time.sleep(5)
        continue

    payload = json.loads(result.stdout)
    items = payload.get("items", [])
    if len(items) != expected_nodes:
        last_error = f"expected {expected_nodes} pods, got {len(items)}"
        time.sleep(5)
        continue

    bad = []
    for item in items:
        name = item.get("metadata", {}).get("name", "unknown")
        labels = item.get("metadata", {}).get("labels", {}) or {}
        revision = labels.get("controller-revision-hash", "")
        if revision != target_revision:
            bad.append(f"{name} revision={revision or 'missing'}")
            continue
        conditions = item.get("status", {}).get("conditions", []) or []
        ready = any(cond.get("type") == "Ready" and cond.get("status") == "True" for cond in conditions)
        if not ready:
            bad.append(f"{name} not Ready")

    if not bad:
        raise SystemExit(0)

    last_error = ", ".join(bad)
    time.sleep(5)

print(last_error or f"timed out waiting for restarted {prefix} pods", file=sys.stderr)
raise SystemExit(1)
PY
}

wait_for_cluster_healthy() {
  kubectl -n "${ns}" wait --for=condition=Ready "pod/${curl_pod}" --timeout=600s >/dev/null

  python3 - "${ns}" "${prefix}" "${service}" "${curl_pod}" "${expected_nodes}" <<'PY'
import json
import subprocess
import sys
import time

ns, prefix, service, curl_pod, expected_nodes = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
service_host = f"{service}.{ns}.svc"
deadline = time.monotonic() + 900
last_error = "cluster did not become healthy"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def curl_json(path):
    result = run(
        [
            "kubectl",
            "-n",
            ns,
            "exec",
            curl_pod,
            "--",
            "curl",
            "-sS",
            "--max-time",
            "20",
            f"http://{service_host}:9200{path}",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"curl exit {result.returncode}"
        return None, detail
    payload = result.stdout.strip()
    if not payload:
        return None, f"empty response for {path}"
    try:
        return json.loads(payload), ""
    except json.JSONDecodeError:
        return None, f"failed to parse JSON from {path}"


while time.monotonic() < deadline:
    errors = []

    pods_result = run(["kubectl", "-n", ns, "get", "pods", "-l", f"app={prefix}", "-o", "json"])
    if pods_result.returncode != 0:
        errors.append(pods_result.stderr.strip() or "failed to read Elasticsearch pods")
    else:
        pods = json.loads(pods_result.stdout).get("items", [])
        if len(pods) != expected_nodes:
            errors.append(f"expected {expected_nodes} pods, got {len(pods)}")
        else:
            for pod in pods:
                name = pod.get("metadata", {}).get("name", "unknown")
                conditions = pod.get("status", {}).get("conditions", []) or []
                ready = any(cond.get("type") == "Ready" and cond.get("status") == "True" for cond in conditions)
                if not ready:
                    errors.append(f"pod {name} is not Ready")

    health, detail = curl_json(
        f"/_cluster/health?wait_for_status=yellow&wait_for_nodes={expected_nodes}&timeout=10s"
    )
    if health is None:
        errors.append(f"cluster health probe failed: {detail}")
    else:
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"cluster health status expected yellow/green, got {status}")
        if health.get("number_of_nodes") != expected_nodes:
            errors.append(f"expected {expected_nodes} nodes, got {health.get('number_of_nodes')}")

    nodes, detail = curl_json("/_cat/nodes?format=json")
    if nodes is None:
        errors.append(f"_cat/nodes probe failed: {detail}")
    elif not isinstance(nodes, list) or len(nodes) != expected_nodes:
        errors.append(f"expected {expected_nodes} nodes in _cat/nodes, got {0 if not isinstance(nodes, list) else len(nodes)}")

    root, detail = curl_json("/")
    if root is None:
        errors.append(f"root endpoint probe failed: {detail}")
    else:
        uuid = root.get("cluster_uuid")
        if not uuid or uuid == "_na_":
            errors.append("cluster UUID not set")

    if not errors:
        raise SystemExit(0)

    last_error = "; ".join(errors)
    time.sleep(5)

print(last_error, file=sys.stderr)
raise SystemExit(1)
PY
}

patch_bootstrap_config "add"
assert_bootstrap_presence "present"
restart_and_wait
wait_for_cluster_healthy

patch_bootstrap_config "remove"
assert_bootstrap_presence "absent"
restart_and_wait
wait_for_cluster_healthy

static_solver_write_submit "bootstrapped Elasticsearch cluster and removed bootstrap-only setting"
