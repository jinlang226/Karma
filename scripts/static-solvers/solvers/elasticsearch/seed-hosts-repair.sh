#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/seed-hosts-repair
# Strategy: native_shell
# Notes: The fault drifts discovery.seed_hosts to the wrong namespace and
# reintroduces cluster.initial_master_nodes before forcing the highest ordinal
# node to rejoin. Restore the correct discovery list, remove the stale bootstrap
# setting, and recreate the failed node so it mounts the repaired config.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-es-cluster}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
configmap_name="${BENCH_PARAM_CONFIGMAP_NAME:-es-config}"
index_name="${BENCH_PARAM_INDEX_NAME:-app-data}"
expected_nodes="${BENCH_PARAM_EXPECTED_NODE_COUNT:-${BENCH_PARAM_EXPECTED_NODES:-3}}"
password_secret="${BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME:-elastic-password}"
password_key="${BENCH_PARAM_ELASTIC_PASSWORD_KEY:-password}"

[[ "${expected_nodes}" =~ ^[0-9]+$ ]] || static_solver_fail "expected node count must be numeric"
(( expected_nodes > 0 )) || static_solver_fail "expected node count must be positive"

read_secret_value() {
  local secret_name="$1"
  local key_name="$2"
  kubectl -n "${ns}" get secret "${secret_name}" -o "jsonpath={.data.${key_name}}" 2>/dev/null | base64 -d 2>/dev/null || true
}

curl_http_code() {
  local -a args=("$@")
  kubectl -n "${ns}" exec "${curl_pod}" -- \
    curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 15 "${args[@]}"
}

curl_exec() {
  if [[ -n "${elastic_password:-}" ]]; then
    kubectl -n "${ns}" exec "${curl_pod}" -- \
      curl -sS -k --connect-timeout 5 --max-time 20 -u "elastic:${elastic_password}" "$@"
  else
    kubectl -n "${ns}" exec "${curl_pod}" -- \
      curl -sS -k --connect-timeout 5 --max-time 20 "$@"
  fi
}

detect_backend_scheme() {
  local output=""
  for scheme in http https; do
    output="$(curl_http_code -k "${scheme}://${service}.${ns}.svc:9200/" 2>/dev/null || true)"
    if [[ "${output}" =~ ^[0-9]{3}$ && "${output}" != "000" ]]; then
      printf '%s\n' "${scheme}"
      return 0
    fi
  done
  static_solver_fail "unable to detect live Elasticsearch backend scheme for ${service}.${ns}.svc"
}

ensure_index_seed() {
  local service_host="${service}.${ns}.svc"
  local deadline=$((SECONDS + 180))
  local count_payload=""

  while (( SECONDS < deadline )); do
    count_payload="$(curl_exec "${backend_scheme}://${service_host}:9200/${index_name}/_count" 2>/dev/null || true)"
    if grep -q '"count"' <<< "${count_payload}"; then
      return 0
    fi

    if curl_exec -XPUT "${backend_scheme}://${service_host}:9200/${index_name}" \
      -H 'Content-Type: application/json' \
      -d '{"settings":{"number_of_shards":1,"number_of_replicas":1}}' \
      >/dev/null 2>&1; then
      curl_exec -XPOST "${backend_scheme}://${service_host}:9200/${index_name}/_doc" \
        -H 'Content-Type: application/json' \
        -d '{"msg":"seed"}' \
        >/dev/null 2>&1 || true
    fi

    count_payload="$(curl_exec "${backend_scheme}://${service_host}:9200/${index_name}/_count" 2>/dev/null || true)"
    if grep -q '"count"' <<< "${count_payload}"; then
      return 0
    fi

    sleep 5
  done

  static_solver_fail "timed out ensuring ${index_name} exists on the recovered cluster"
}

repair_configmap() {
  python3 - "${ns}" "${configmap_name}" "${expected_nodes}" "${prefix}" <<'PY' | kubectl -n "${ns}" apply -f -
import json
import subprocess
import sys

ns, configmap_name, expected_nodes, prefix = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
result = subprocess.run(
    ["kubectl", "-n", ns, "get", "configmap", configmap_name, "-o", "json"],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if result.returncode != 0:
    raise SystemExit(result.stderr.strip() or f"failed to read {configmap_name}")

obj = json.loads(result.stdout)
text = obj["data"]["elasticsearch.yml"]
lines = text.splitlines()
updated = []
seed_written = False
node_name_written = False
i = 0

while i < len(lines):
    stripped = lines[i].strip()
    if stripped.startswith("node.name:"):
        updated.append("node.name: ${POD_NAME}")
        node_name_written = True
        i += 1
        continue
    if stripped.startswith("discovery.seed_hosts:"):
        updated.append("discovery.seed_hosts:")
        for ordinal in range(expected_nodes):
            updated.append(f"  - {prefix}-{ordinal}.{prefix}")
        seed_written = True
        i += 1
        while i < len(lines) and lines[i].lstrip().startswith("- "):
            i += 1
        continue
    if stripped.startswith("cluster.initial_master_nodes:"):
        i += 1
        while i < len(lines) and lines[i].lstrip().startswith("- "):
            i += 1
        continue
    updated.append(lines[i])
    i += 1

if not seed_written:
    if updated and updated[-1].strip():
        updated.append("")
    updated.append("discovery.seed_hosts:")
    for ordinal in range(expected_nodes):
        updated.append(f"  - {prefix}-{ordinal}.{prefix}")

if not node_name_written:
    insert_at = 1 if updated and updated[0].startswith("cluster.name:") else 0
    updated.insert(insert_at, "node.name: ${POD_NAME}")

obj["data"]["elasticsearch.yml"] = "\n".join(updated).rstrip() + "\n"
for key in ("managedFields", "resourceVersion", "uid", "creationTimestamp"):
    obj.get("metadata", {}).pop(key, None)
obj.pop("status", None)
print(json.dumps(obj))
PY
}

assert_configmap_repaired() {
  local config_text=""
  config_text="$(
    kubectl -n "${ns}" get configmap "${configmap_name}" -o jsonpath='{.data.elasticsearch\.yml}'
  )"

  grep -q 'discovery\.seed_hosts:' <<< "${config_text}" ||
    static_solver_fail "discovery.seed_hosts missing from ${configmap_name}"
  grep -q "${prefix}-0\.${prefix}" <<< "${config_text}" ||
    static_solver_fail "repaired discovery.seed_hosts does not include ${prefix}-0.${prefix}"
  if grep -q '\.default\.svc\.cluster\.local' <<< "${config_text}"; then
    static_solver_fail "discovery.seed_hosts still references the default namespace"
  fi
  if grep -q 'cluster\.initial_master_nodes' <<< "${config_text}"; then
    static_solver_fail "cluster.initial_master_nodes is still present in ${configmap_name}"
  fi
}

wait_for_cluster_healthy() {
  kubectl -n "${ns}" wait --for=condition=Ready "pod/${curl_pod}" --timeout=600s >/dev/null

  python3 - "${ns}" "${prefix}" "${service}" "${curl_pod}" "${1}" "${index_name}" "${2}" "${3}" <<'PY'
import json
import subprocess
import sys
import time

ns, prefix, service, curl_pod, expected_nodes, index_name, backend_scheme, elastic_password = (
    sys.argv[1],
    sys.argv[2],
    sys.argv[3],
    sys.argv[4],
    int(sys.argv[5]),
    sys.argv[6],
    sys.argv[7],
    sys.argv[8],
)
service_host = f"{service}.{ns}.svc"
deadline = time.monotonic() + 900
last_error = "cluster did not recover"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def curl_json(path):
    cmd = [
        "kubectl",
        "-n",
        ns,
        "exec",
        curl_pod,
        "--",
        "curl",
        "-sS",
        "-k",
        "--max-time",
        "20",
    ]
    if elastic_password:
        cmd.extend(["-u", f"elastic:{elastic_password}"])
    cmd.append(f"{backend_scheme}://{service_host}:9200{path}")
    result = run(cmd)
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

    nodes, detail = curl_json("/_cat/nodes?format=json")
    if nodes is None:
        errors.append(f"_cat/nodes probe failed: {detail}")
    elif not isinstance(nodes, list) or len(nodes) != expected_nodes:
        errors.append(f"expected {expected_nodes} nodes in _cat/nodes, got {0 if not isinstance(nodes, list) else len(nodes)}")

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

    count, detail = curl_json(f"/{index_name}/_count")
    if count is None:
        errors.append(f"{index_name} count probe failed: {detail}")
    elif "count" not in count:
        errors.append(f"unable to verify {index_name} count")

    if not errors:
        raise SystemExit(0)

    last_error = "; ".join(errors)
    time.sleep(5)

print(last_error, file=sys.stderr)
raise SystemExit(1)
PY
}

repair_configmap
assert_configmap_repaired

backend_scheme="$(detect_backend_scheme)"
elastic_password="$(read_secret_value "${password_secret}" "${password_key}")"

kubectl -n "${ns}" scale "statefulset/${prefix}" --replicas="${expected_nodes}" >/dev/null
kubectl -n "${ns}" delete "pod/${prefix}-$((expected_nodes - 1))" --ignore-not-found=true >/dev/null || true

ensure_index_seed
wait_for_cluster_healthy "${expected_nodes}" "${backend_scheme}" "${elastic_password}"

static_solver_write_submit "repaired Elasticsearch seed hosts and restored cluster membership"
