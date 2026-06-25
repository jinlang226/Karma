#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/rollback-rehearsal
# Strategy: native_shell
# Notes: Read-only rollback planner. It captures live cluster/index settings and
# writes a review-only rollback script to ConfigMap/rollback-rehearsal without
# executing any changes.

static_solver_export_namespace_if_unset "elasticsearch"

ns="${BENCH_NAMESPACE}"
service="${BENCH_PARAM_HTTP_SERVICE_NAME:-es-http}"
curl_pod="${BENCH_PARAM_CURL_POD_NAME:-curl-test}"
ops_user="${BENCH_PARAM_OPS_USER:-ops-user}"
ops_password="${BENCH_PARAM_OPS_PASSWORD:-opspass}"
elastic_secret="${BENCH_PARAM_CURRENT_PASSWORD_SECRET_NAME:-${BENCH_PARAM_ELASTIC_PASSWORD_SECRET_NAME:-elastic-password}}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

elastic_password=""
if kubectl -n "${ns}" get secret "${elastic_secret}" >/dev/null 2>&1; then
  elastic_password="$(
    kubectl -n "${ns}" get secret "${elastic_secret}" -o jsonpath='{.data.password}' 2>/dev/null | base64 -d 2>/dev/null || true
  )"
fi

base_url=""
auth_desc="unauthenticated"
auth_type="none"
auth_value=""

try_endpoint() {
  local scheme="$1"
  shift
  kubectl -n "${ns}" exec "${curl_pod}" -- \
    curl -s -S -k --max-time 8 "$@" "${scheme}://${service}.${ns}.svc:9200/_cluster/health" 2>/dev/null || true
}

for scheme in https http; do
  output="$(try_endpoint "${scheme}")"
  if [[ "${output}" == *'"status"'* ]]; then
    base_url="${scheme}://${service}.${ns}.svc:9200"
    auth_desc="unauthenticated"
    auth_type="none"
    auth_value=""
    break
  fi

  output="$(try_endpoint "${scheme}" -u "${ops_user}:${ops_password}")"
  if [[ "${output}" == *'"status"'* ]]; then
    base_url="${scheme}://${service}.${ns}.svc:9200"
    auth_desc="ops-user"
    auth_type="basic"
    auth_value="${ops_user}:${ops_password}"
    break
  fi

  if [[ -n "${elastic_password}" ]]; then
    output="$(try_endpoint "${scheme}" -u "elastic:${elastic_password}")"
    if [[ "${output}" == *'"status"'* ]]; then
      base_url="${scheme}://${service}.${ns}.svc:9200"
      auth_desc="elastic-secret"
      auth_type="basic"
      auth_value="elastic:${elastic_password}"
      break
    fi
  fi

  output="$(try_endpoint "${scheme}" -u "elastic:elasticpass")"
  if [[ "${output}" == *'"status"'* ]]; then
    base_url="${scheme}://${service}.${ns}.svc:9200"
    auth_desc="elastic-default"
    auth_type="basic"
    auth_value="elastic:elasticpass"
    break
  fi
done

plan_curl() {
  local path="$1"
  if [[ -z "${base_url}" ]]; then
    return 0
  fi
  if [[ "${auth_type}" == "basic" ]]; then
    kubectl -n "${ns}" exec "${curl_pod}" -- \
      curl -s -S -k --max-time 15 -u "${auth_value}" "${base_url}${path}" || true
  else
    kubectl -n "${ns}" exec "${curl_pod}" -- \
      curl -s -S -k --max-time 15 "${base_url}${path}" || true
  fi
}

kubectl -n "${ns}" get pods -o wide > "${tmp_dir}/pods.txt" 2>&1 || true
kubectl -n "${ns}" get statefulsets > "${tmp_dir}/statefulsets.txt" 2>&1 || true
kubectl -n "${ns}" get svc > "${tmp_dir}/services.txt" 2>&1 || true

plan_curl "/_cluster/settings?flat_settings=true&pretty" > "${tmp_dir}/cluster-settings.json"
plan_curl "/_all/_settings?flat_settings=true&pretty" > "${tmp_dir}/index-settings.json"
plan_curl "/_ilm/policy?pretty" > "${tmp_dir}/ilm-policies.json"
plan_curl "/_nodes?filter_path=nodes.*.name,nodes.*.version,nodes.*.roles,nodes.*.settings.node.attr&pretty" > "${tmp_dir}/nodes.json"

script_path="${STATIC_SOLVER_STAGE_DIR}/rollback.sh"
python3 - "${tmp_dir}" "${ns}" "${service}" "${base_url}" "${auth_desc}" "${auth_type}" "${auth_value}" > "${script_path}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

tmp = Path(sys.argv[1])
namespace = sys.argv[2]
service = sys.argv[3]
base_url = sys.argv[4]
auth_desc = sys.argv[5]
auth_type = sys.argv[6]
auth_value = sys.argv[7]


def load_text(name: str) -> str:
    path = tmp / name
    if not path.exists():
        return ""
    return path.read_text(errors="ignore").strip()


def load_json(name: str):
    raw = load_text(name)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def ensure_dict(value):
    return value if isinstance(value, dict) else {}


cluster_settings = ensure_dict(load_json("cluster-settings.json"))
index_settings = ensure_dict(load_json("index-settings.json"))
ilm_policies = ensure_dict(load_json("ilm-policies.json"))
nodes = ensure_dict(load_json("nodes.json"))
pods_text = load_text("pods.txt")
statefulsets_text = load_text("statefulsets.txt")
services_text = load_text("services.txt")


def shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


persistent = cluster_settings.get("persistent") or {}
transient = cluster_settings.get("transient") or {}
cluster_payload = {
    "persistent": {key: None for key in persistent},
    "transient": {key: None for key in transient},
}

dynamic_index_prefixes = (
    "index.routing.",
    "index.blocks.",
    "index.lifecycle.",
    "index.priority",
    "index.refresh_interval",
    "index.auto_expand_replicas",
    "index.number_of_replicas",
    "index.codec",
    "index.translog.",
    "index.write.wait_for_active_shards",
)
review_only_prefixes = (
    "index.number_of_shards",
    "index.routing_partition_size",
    "index.soft_deletes.",
    "index.mode",
    "index.sort.",
)

print("#!/usr/bin/env bash")
print("set -euo pipefail")
print()
print("# Review-only rollback rehearsal script for Elasticsearch.")
print("# Generated by the static solver. Do not execute blindly; review every")
print("# command and payload against the current cluster state first.")
print(f'NS="${{NS:-{namespace}}}"')
print(f'SERVICE="${{SERVICE:-{service}}}"')
print('CURL_POD="${CURL_POD:-curl-test}"')
print(f'BASE_URL="${{BASE_URL:-{base_url or f"https://{service}.{namespace}.svc:9200"}}}"')
print()
if auth_type == "basic":
    print(f'AUTH_VALUE={shell_single_quote(auth_value)}')
    print('AUTH_ARGS=(-u "${AUTH_VALUE}")')
else:
    print("AUTH_ARGS=()")
print()
print("es_curl() {")
print('  kubectl -n "${NS}" exec "${CURL_POD}" -- \\')
print('    curl -s -S -k --max-time 20 "${AUTH_ARGS[@]}" "$@"')
print("}")
print()
print("# Captured context")
print(f"# Auth mode used to inspect the cluster: {auth_desc}")
for line in (pods_text.splitlines()[:12] + statefulsets_text.splitlines()[:8] + services_text.splitlines()[:8]):
    if line.strip():
        print(f"# {line}")
print()
print("# 1. Reset cluster-level overrides back to defaults.")
print("#    Review the payload first; null means RESET / remove override.")
print(f"cat > /tmp/es-cluster-reset.json <<'EOF_JSON'\n{json.dumps(cluster_payload, indent=2)}\nEOF_JSON")
print('es_curl -XPUT "${BASE_URL}/_cluster/settings" -H "Content-Type: application/json" \\')
print('  --data-binary @/tmp/es-cluster-reset.json')
print()
print("# 2. Review per-index rollback candidates.")
if isinstance(index_settings, dict) and index_settings:
    for index_name in sorted(index_settings):
        entry = index_settings.get(index_name, {})
        if not isinstance(entry, dict):
            print(f"# Index: {index_name}")
            print(f"# Unexpected index payload type: {type(entry).__name__}")
            print()
            continue
        settings_root = entry.get("settings", {})
        if not isinstance(settings_root, dict):
            print(f"# Index: {index_name}")
            print("# Missing settings object in captured payload.")
            print()
            continue
        settings = settings_root.get("index", {})
        if not isinstance(settings, dict):
            print(f"# Index: {index_name}")
            print("# Missing index settings object in captured payload.")
            print()
            continue
        flat_settings = {}
        for key, value in settings.items():
            if isinstance(value, dict):
                continue
            flat_settings[f"index.{key}"] = value
        for group_key in ("routing", "blocks", "lifecycle", "translog", "soft_deletes", "sort"):
            group = settings.get(group_key)
            if isinstance(group, dict):
                for subkey, subvalue in group.items():
                    if isinstance(subvalue, dict):
                        for subsubkey, subsubvalue in subvalue.items():
                            if isinstance(subsubvalue, dict):
                                for leaf_key, leaf_value in subsubvalue.items():
                                    flat_settings[f"index.{group_key}.{subkey}.{subsubkey}.{leaf_key}"] = leaf_value
                            else:
                                flat_settings[f"index.{group_key}.{subkey}.{subsubkey}"] = subsubvalue
                    else:
                        flat_settings[f"index.{group_key}.{subkey}"] = subvalue
        rollback_payload = {
            key: None
            for key in sorted(flat_settings)
            if key.startswith(dynamic_index_prefixes)
        }
        review_only = [
            key for key in sorted(flat_settings)
            if key.startswith(review_only_prefixes)
        ]
        safe_index_name = index_name.replace("/", "_")
        print(f"# Index: {index_name}")
        if rollback_payload:
            print(f"cat > /tmp/{safe_index_name}-rollback.json <<'EOF_JSON'")
            print(json.dumps(rollback_payload, indent=2))
            print("EOF_JSON")
            print(f'es_curl -XPUT "${{BASE_URL}}/{index_name}/_settings" -H "Content-Type: application/json" \\')
            print(f'  --data-binary @/tmp/{safe_index_name}-rollback.json')
        else:
            print("# No dynamic index settings were auto-selected for rollback.")
        if review_only:
            print("# Manual review required for immutable or risky settings:")
            for key in review_only:
                print(f"#   - {key} = {flat_settings[key]}")
        print()
else:
    print("# No index settings payload was captured.")
    print()

print("# 3. Review ILM policies before removing or replacing them.")
if isinstance(ilm_policies, dict) and ilm_policies:
    for policy_name in sorted(ilm_policies)[:20]:
        print(f"#   - {policy_name}")
else:
    print("#   - none captured")
print()
print("# 4. Node snapshot used for rollback review.")
node_map = nodes.get("nodes") or {}
for node_id, node in sorted(node_map.items()):
    if not isinstance(node, dict):
        print(f"#   - {node_id} unexpected node payload type={type(node).__name__}")
        continue
    roles = node.get("roles", [])
    if not isinstance(roles, list):
        roles = []
    print(f"#   - {node.get('name', node_id)} version={node.get('version', 'unknown')} roles={','.join(str(role) for role in roles)}")
PY

kubectl -n "${ns}" create configmap rollback-rehearsal \
  --from-file=rollback.sh="${script_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "prepared Elasticsearch rollback rehearsal script without executing it"
