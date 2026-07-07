#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: nginx-ingress/change-plan-only
# Strategy: native_shell
# Notes: Review-only change-plan generator. It inspects the controller,
# ConfigMap, and app Ingresses, then writes a plan.md into the change-plan
# ConfigMap without mutating the running cluster.

static_solver_export_nginx_defaults

app_ns="${BENCH_NS_APP}"
ingress_ns="${BENCH_NS_INGRESS}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

kubectl -n "${ingress_ns}" get deploy ingress-nginx-controller -o json > "${tmp_dir}/deploy.json" 2>/dev/null || printf '{}\n' > "${tmp_dir}/deploy.json"
kubectl -n "${ingress_ns}" get configmap ingress-nginx-controller -o json > "${tmp_dir}/configmap.json" 2>/dev/null || printf '{}\n' > "${tmp_dir}/configmap.json"
kubectl -n "${app_ns}" get ingress -o json > "${tmp_dir}/ingress.json" 2>/dev/null || printf '{"items":[]}\n' > "${tmp_dir}/ingress.json"

plan_path="${STATIC_SOLVER_STAGE_DIR}/plan.md"
python3 - "${tmp_dir}" "${app_ns}" "${ingress_ns}" > "${plan_path}" <<'PY'
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

ingresses: list[tuple[str, str, list[str]]] = []
for item in items:
    if not isinstance(item, dict):
        continue
    metadata = item.get("metadata") or {}
    spec = item.get("spec") or {}
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        continue
    name = str(metadata.get("name") or "")
    annotations = metadata.get("annotations") or {}
    if not isinstance(annotations, dict):
        annotations = {}
    host = ""
    rules = spec.get("rules") or []
    if isinstance(rules, list) and rules:
        first = rules[0]
        if isinstance(first, dict):
            host = str(first.get("host") or "")
    nginx_keys = sorted(
        str(key) for key in annotations
        if str(key).startswith("nginx.ingress.kubernetes.io/")
    )
    ingresses.append((name, host, nginx_keys))

print("# ingress-nginx change plan")
print()
print("## Objective")
print()
print("Prepare the next ingress-nginx upgrade without mutating the live cluster during planning.")
print()
print("## Current observed state")
print()
print(f"- Controller namespace: `{ingress_ns}`")
print(f"- Application namespace: `{app_ns}`")
print(f"- Controller image: `{image}`")
if args:
    print("- Controller args:")
    for arg in args:
        print(f"  - `{arg}`")
else:
    print("- Controller args: none captured")
if data:
    print("- ingress-nginx ConfigMap data keys:")
    for key in sorted(data):
        print(f"  - `{key}`")
else:
    print("- ingress-nginx ConfigMap data keys: none captured")
if ingresses:
    print("- Application Ingresses:")
    for name, host, nginx_keys in ingresses:
        host_text = host or "no host captured"
        print(f"  - `{name}` host `{host_text}`")
        if nginx_keys:
            for key in nginx_keys:
                print(f"    - annotation `{key}`")
else:
    print("- Application Ingresses: none captured")
print()
print("## Planned upgrade sequence")
print()
print("1. Export the current controller Deployment and ConfigMap as rollback inputs.")
print(f"2. Review the controller image `{image}` and select the target ingress-nginx release.")
print("3. Diff controller args against the target release defaults and preserve only the settings still required.")
print("4. Diff ingress-nginx ConfigMap keys against the target release defaults and document which keys stay, change, or drop.")
print("5. Review each application Ingress for nginx-specific annotations and verify target-release compatibility.")
print("6. Apply the controller Deployment upgrade in the ingress namespace during the maintenance window.")
print("7. Re-apply the reviewed ConfigMap keys and controller args, then restart the controller if required.")
print("8. Validate the application Ingress routes from the curl-test pod before ending the maintenance window.")
print()
print("## Validation checklist")
print()
print(f"- `kubectl -n {ingress_ns} rollout status deploy/ingress-nginx-controller --timeout=180s`")
print(f"- `kubectl -n {ingress_ns} get configmap ingress-nginx-controller -o yaml`")
print(f"- `kubectl -n {app_ns} get ingress -o yaml`")
print(f"- `kubectl -n {ingress_ns} logs deploy/ingress-nginx-controller --tail=200`")
print()
print("## Rollback readiness")
print()
print(f"- Save a pre-upgrade copy of `deploy/ingress-nginx-controller` in namespace `{ingress_ns}`.")
print(f"- Save a pre-upgrade copy of `configmap/ingress-nginx-controller` in namespace `{ingress_ns}`.")
print("- Record the current nginx-specific annotations on every application Ingress before the upgrade.")
PY

kubectl -n "${app_ns}" create configmap change-plan \
  --from-file=plan.md="${plan_path}" \
  --dry-run=client -o yaml | kubectl -n "${app_ns}" apply -f -

static_solver_write_submit "prepared ingress-nginx change plan without executing changes"
