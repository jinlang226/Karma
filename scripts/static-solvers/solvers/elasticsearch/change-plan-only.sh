#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/change-plan-only
# Strategy: native_shell
# Notes: Read-only planner. It inspects the live cluster, summarizes current
# state, and writes a markdown migration plan to ConfigMap/change-plan without
# mutating Elasticsearch itself.

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
auth_args=()

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
    auth_args=()
    break
  fi

  output="$(try_endpoint "${scheme}" -u "${ops_user}:${ops_password}")"
  if [[ "${output}" == *'"status"'* ]]; then
    base_url="${scheme}://${service}.${ns}.svc:9200"
    auth_desc="ops-user"
    auth_args=(-u "${ops_user}:${ops_password}")
    break
  fi

  if [[ -n "${elastic_password}" ]]; then
    output="$(try_endpoint "${scheme}" -u "elastic:${elastic_password}")"
    if [[ "${output}" == *'"status"'* ]]; then
      base_url="${scheme}://${service}.${ns}.svc:9200"
      auth_desc="elastic-secret"
      auth_args=(-u "elastic:${elastic_password}")
      break
    fi
  fi

  output="$(try_endpoint "${scheme}" -u "elastic:elasticpass")"
  if [[ "${output}" == *'"status"'* ]]; then
    base_url="${scheme}://${service}.${ns}.svc:9200"
    auth_desc="elastic-default"
    auth_args=(-u "elastic:elasticpass")
    break
  fi
done

plan_curl() {
  local path="$1"
  if [[ -z "${base_url}" ]]; then
    return 0
  fi
  if [[ ${#auth_args[@]} -gt 0 ]]; then
    kubectl -n "${ns}" exec "${curl_pod}" -- \
      curl -s -S -k --max-time 15 "${auth_args[@]}" "${base_url}${path}" || true
  else
    kubectl -n "${ns}" exec "${curl_pod}" -- \
      curl -s -S -k --max-time 15 "${base_url}${path}" || true
  fi
}

kubectl -n "${ns}" get pods -o wide > "${tmp_dir}/pods.txt" 2>&1 || true
kubectl -n "${ns}" get statefulsets > "${tmp_dir}/statefulsets.txt" 2>&1 || true
kubectl -n "${ns}" get svc > "${tmp_dir}/services.txt" 2>&1 || true
kubectl -n "${ns}" get configmap es-config -o "jsonpath={.data.elasticsearch\.yml}" > "${tmp_dir}/es-config.yml" 2>/dev/null || true

plan_curl "/" > "${tmp_dir}/root.json"
plan_curl "/_cluster/settings?flat_settings=true&pretty" > "${tmp_dir}/cluster-settings.json"
plan_curl "/_all/_settings?flat_settings=true&pretty" > "${tmp_dir}/index-settings.json"
plan_curl "/_index_template?pretty" > "${tmp_dir}/index-templates.json"
plan_curl "/_ilm/policy?pretty" > "${tmp_dir}/ilm-policies.json"
plan_curl "/_nodes?filter_path=nodes.*.name,nodes.*.version,nodes.*.roles,nodes.*.settings.node.attr&pretty" > "${tmp_dir}/nodes.json"
plan_curl "/_security/role?pretty" > "${tmp_dir}/security-roles.json"

plan_path="${tmp_dir}/plan.md"
python3 - "${tmp_dir}" "${ns}" "${service}" "${auth_desc}" "${base_url}" > "${plan_path}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

tmp = Path(sys.argv[1])
namespace = sys.argv[2]
service = sys.argv[3]
auth_desc = sys.argv[4]
base_url = sys.argv[5]


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


root = load_json("root.json") or {}
nodes = load_json("nodes.json") or {}
cluster_settings = load_json("cluster-settings.json") or {}
index_settings = load_json("index-settings.json") or {}
index_templates = load_json("index-templates.json") or {}
ilm_policies = load_json("ilm-policies.json") or {}
security_roles = load_json("security-roles.json") or {}
es_config = load_text("es-config.yml")
pods_text = load_text("pods.txt")
statefulsets_text = load_text("statefulsets.txt")
services_text = load_text("services.txt")

cluster_name = root.get("cluster_name", "unknown")
version = (root.get("version") or {}).get("number", "unknown")
node_map = nodes.get("nodes") or {}
node_count = len(node_map)
node_versions = sorted({node.get("version", "unknown") for node in node_map.values()})
node_roles = sorted({role for node in node_map.values() for role in node.get("roles", [])})

persistent = sorted((cluster_settings.get("persistent") or {}).keys())
transient = sorted((cluster_settings.get("transient") or {}).keys())
indices = sorted(index_settings.keys()) if isinstance(index_settings, dict) else []
template_items = index_templates.get("index_templates") or []
template_names = sorted(
    item.get("name", "unknown") for item in template_items if isinstance(item, dict)
)
ilm_names = sorted((ilm_policies or {}).keys()) if isinstance(ilm_policies, dict) else []
role_names = sorted((security_roles or {}).keys()) if isinstance(security_roles, dict) else []
security_lines = [
    line.strip()
    for line in es_config.splitlines()
    if line.strip().startswith("xpack.security.")
]

upgrade_targets = [
    "Confirm image/tag compatibility and plugin compatibility before changing StatefulSet images.",
    "Snapshot data and export security / template / ILM metadata before any rollout.",
    "Preserve current TLS and auth settings during the upgrade, then validate them again after each restart.",
    "Roll one node set at a time and wait for green/yellow cluster health plus shard recovery between steps.",
    "Re-run password, file realm, and audit checks after the upgrade to confirm no security regression.",
]

print("# Elasticsearch Change / Migration Plan")
print()
print("## Current State Summary")
print(f"- Namespace: `{namespace}`")
print(f"- Service endpoint: `{base_url or f'http://{service}.{namespace}.svc:9200'}`")
print(f"- Detected auth mode: `{auth_desc}`")
print(f"- Cluster name: `{cluster_name}`")
print(f"- Detected version: `{version}`")
print(f"- Detected node count: `{node_count}`")
print(f"- Node versions seen: `{', '.join(node_versions) if node_versions else 'unknown'}`")
print(f"- Node roles seen: `{', '.join(node_roles) if node_roles else 'unknown'}`")
print(
    f"- Persistent cluster settings: `{', '.join(persistent[:8]) if persistent else 'none detected'}`"
)
print(
    f"- Transient cluster settings: `{', '.join(transient[:8]) if transient else 'none detected'}`"
)
print(f"- Indices observed: `{', '.join(indices[:8]) if indices else 'none detected'}`")
print(
    f"- Index templates observed: `{', '.join(template_names[:8]) if template_names else 'none detected'}`"
)
print(f"- ILM policies observed: `{', '.join(ilm_names[:8]) if ilm_names else 'none detected'}`")
print(f"- Security roles observed: `{', '.join(role_names[:8]) if role_names else 'none detected'}`")
print()
print("## Upgrade Planning Steps")
for idx, step in enumerate(upgrade_targets, start=1):
    print(f"{idx}. {step}")
print("6. Capture a before/after diff of cluster settings, index settings, templates, and ILM policies; do not change them during planning.")
print("7. Pre-stage rollback artifacts: current container image tags, current TLS secrets/configmaps, password secret names, and any file-realm mounts.")
print("8. Schedule the actual upgrade window only after this plan is reviewed and a dry-run checklist is approved.")
print()
print("## Validation Checklist For The Real Upgrade")
print("- Verify every StatefulSet pod returns Ready after each restart.")
print("- Verify `_cluster/health` stays yellow/green during the rollout.")
print("- Verify authentication still works for the active operators and service accounts.")
print("- Verify all pre-existing index settings and shard-allocation rules remain unchanged.")
print("- Verify templates, ILM policies, and security roles still match the captured baseline.")
print()
print("## Captured Evidence")
print("### Kubernetes Objects")
print("```text")
print(pods_text[:2000] or "pods unavailable")
print("```")
print("```text")
print(statefulsets_text[:1200] or "statefulsets unavailable")
print("```")
print("```text")
print(services_text[:1200] or "services unavailable")
print("```")
if security_lines:
    print("### Security Settings Snapshot")
    print("```yaml")
    print("\n".join(security_lines[:20]))
    print("```")
if persistent or transient:
    print("### Cluster Settings Snapshot")
    print("```json")
    print(
        json.dumps(
            {
                "persistent": {k: cluster_settings.get("persistent", {}).get(k) for k in persistent[:12]},
                "transient": {k: cluster_settings.get("transient", {}).get(k) for k in transient[:12]},
            },
            indent=2,
        )
    )
    print("```")
if indices:
    print("### Indexes Requiring Preservation")
    for index_name in indices[:12]:
        print(f"- `{index_name}`")
PY

kubectl -n "${ns}" create configmap change-plan \
  --from-file=plan.md="${plan_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "captured Elasticsearch change plan without mutating live settings"
