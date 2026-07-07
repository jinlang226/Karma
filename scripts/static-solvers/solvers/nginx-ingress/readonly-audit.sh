#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: nginx-ingress/readonly-audit
# Strategy: native_shell
# Notes: Read-only audit generator. It inspects the live ingress-nginx
# controller and app Ingresses, then stores findings in the config-audit
# ConfigMap without changing the cluster.

static_solver_export_nginx_defaults

app_ns="${BENCH_NS_APP}"
ingress_ns="${BENCH_NS_INGRESS}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

kubectl -n "${ingress_ns}" get deploy ingress-nginx-controller -o json > "${tmp_dir}/deploy.json" 2>/dev/null || printf '{}\n' > "${tmp_dir}/deploy.json"
kubectl -n "${ingress_ns}" get configmap ingress-nginx-controller -o json > "${tmp_dir}/configmap.json" 2>/dev/null || printf '{}\n' > "${tmp_dir}/configmap.json"
kubectl -n "${app_ns}" get ingress -o json > "${tmp_dir}/ingress.json" 2>/dev/null || printf '{"items":[]}\n' > "${tmp_dir}/ingress.json"

findings_path="${STATIC_SOLVER_STAGE_DIR}/findings.txt"
python3 - "${tmp_dir}" "${app_ns}" "${ingress_ns}" > "${findings_path}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

tmp = Path(sys.argv[1])
app_ns = sys.argv[2]
ingress_ns = sys.argv[3]


def load_json(name: str):
    path = tmp / name
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(errors="ignore"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


deploy = load_json("deploy.json")
configmap = load_json("configmap.json")
ingress_list = load_json("ingress.json")

containers = (((deploy.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
controller = containers[0] if containers and isinstance(containers[0], dict) else {}
image = str(controller.get("image") or "unknown")
args = controller.get("args") or []
if not isinstance(args, list):
    args = []
args = [str(arg) for arg in args]

data = configmap.get("data") or {}
if not isinstance(data, dict):
    data = {}

items = ingress_list.get("items") or []
if not isinstance(items, list):
    items = []

print(f"ingress-nginx readonly audit for controller namespace {ingress_ns} and app namespace {app_ns}")
print()
print(f"Controller image: {image}")
if args:
    print("Controller arguments:")
    for arg in args:
        print(f"- {arg}")
else:
    print("Controller arguments: none captured")
print()

if data:
    print("Controller ConfigMap data keys:")
    for key in sorted(data):
        print(f"- {key}")
else:
    print("Controller ConfigMap data keys: none captured")
print()

if items:
    print("Ingress findings:")
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") or {}
        spec = item.get("spec") or {}
        if not isinstance(metadata, dict) or not isinstance(spec, dict):
            continue
        name = str(metadata.get("name") or "unknown")
        rules = spec.get("rules") or []
        host = ""
        if isinstance(rules, list) and rules:
            first = rules[0]
            if isinstance(first, dict):
                host = str(first.get("host") or "")
        annotations = metadata.get("annotations") or {}
        if not isinstance(annotations, dict):
            annotations = {}
        nginx_keys = sorted(
            str(key) for key in annotations
            if str(key).startswith("nginx.ingress.kubernetes.io/")
        )
        print(f"- Ingress {name} host={host or 'n/a'}")
        if nginx_keys:
            for key in nginx_keys:
                print(f"  - annotation {key}={annotations.get(key)}")
        else:
            print("  - no nginx-specific annotations captured")
else:
    print("Ingress findings: none captured")
print()
print("Audit summary:")
print("- This report is read-only and no live changes were applied.")
print("- Review controller args, ConfigMap keys, and ingress annotations for upgrade compatibility and policy compliance.")
PY

kubectl -n "${app_ns}" create configmap config-audit \
  --from-file=findings.txt="${findings_path}" \
  --dry-run=client -o yaml | kubectl -n "${app_ns}" apply -f -

static_solver_write_submit "prepared ingress-nginx readonly audit findings without mutating the cluster"
