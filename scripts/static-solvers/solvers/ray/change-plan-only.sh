#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: ray/change-plan-only
# Strategy: native_shell
# Notes: Review-only planner. It snapshots the live Ray state into
# ConfigMap/change-plan without mutating the cluster.

static_solver_export_namespace_if_unset "ray"

ns="${BENCH_NAMESPACE}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

capture_cmd() {
  local name="${1:?output name is required}"
  shift
  "$@" > "${tmp_dir}/${name}" 2>&1 || true
}

capture_cmd pods.txt kubectl -n "${ns}" get pods -o wide
capture_cmd deployments.json kubectl -n "${ns}" get deploy ray-head ray-worker -o json
capture_cmd service.json kubectl -n "${ns}" get svc ray-head -o json
capture_cmd service.yaml kubectl -n "${ns}" get svc ray-head -o yaml

plan_path="${STATIC_SOLVER_STAGE_DIR}/plan.md"
python3 - "${tmp_dir}" > "${plan_path}" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

tmp = Path(sys.argv[1])
default_image = "rayproject/ray:2.9.0"
default_workers = 1
default_service_ports = {6379}


def load_json(name: str) -> dict:
    path = tmp / name
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(errors="ignore"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def deployment_item(payload: dict, name: str) -> dict:
    for item in payload.get("items", []) or []:
        metadata = item.get("metadata") or {}
        if metadata.get("name") == name:
            return item if isinstance(item, dict) else {}
    return {}


def container(item: dict) -> dict:
    containers = (((item.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    if not containers or not isinstance(containers[0], dict):
        return {}
    return containers[0]


def service_ports(payload: dict) -> list[int]:
    ports: list[int] = []
    for item in ((payload.get("spec") or {}).get("ports") or []):
        if not isinstance(item, dict):
            continue
        try:
            ports.append(int(item.get("port")))
        except (TypeError, ValueError):
            continue
    return ports


deployments = load_json("deployments.json")
service = load_json("service.json")
head = container(deployment_item(deployments, "ray-head"))
worker = container(deployment_item(deployments, "ray-worker"))
worker_replicas = int(((deployment_item(deployments, "ray-worker").get("spec") or {}).get("replicas", 0)) or 0)
head_image = str(head.get("image") or default_image)
worker_image = str(worker.get("image") or default_image)
ports = service_ports(service)
extra_ports = [port for port in ports if port not in default_service_ports]

lines: list[str] = []
lines.append("# Ray Change / Migration Plan")
lines.append("")
lines.append("## Current State")
lines.append(f"- Worker replicas: `{worker_replicas}`")
lines.append(f"- ray-head image: `{head_image}`")
lines.append(f"- ray-worker image: `{worker_image}`")
lines.append(f"- ray-head service ports: `{', '.join(str(port) for port in ports) or 'none captured'}`")
lines.append("")
lines.append("## Proposed Review-Only Changes")
lines.append("1. Reconfirm the live worker count, deployment images, and service ports immediately before the next change window.")
lines.append(f"2. If the cluster should return to the baseline worker count of `{default_workers}`, plan a reviewed scale-down rather than doing it now.")
lines.append(f"3. If the cluster should return to the baseline image `{default_image}`, plan a reviewed image rollback for both `ray-head` and `ray-worker`.")
if extra_ports:
    lines.append(f"4. Review whether the non-default service ports `{', '.join(str(port) for port in extra_ports)}` should be removed from `ray-head` in the next window.")
else:
    lines.append("4. No non-default service ports were captured; keep the current `ray-head` service manifest under review only.")
lines.append("5. Keep all commands below as review-only artifacts. Do not run them now.")
lines.append("")
lines.append("## Commands To Review (Do Not Run Now)")
lines.append("```bash")
lines.append(f"kubectl -n ray scale deploy/ray-worker --replicas={default_workers}")
lines.append(f"kubectl -n ray set image deploy/ray-head ray-head={default_image}")
lines.append(f"kubectl -n ray set image deploy/ray-worker ray-worker={default_image}")
lines.append("kubectl -n ray get svc ray-head -o yaml > /tmp/ray-head.change-window.yaml")
lines.append("kubectl -n ray apply -f /tmp/ray-head.change-window.yaml")
lines.append("kubectl -n ray rollout status deploy/ray-head --timeout=300s")
lines.append("kubectl -n ray rollout status deploy/ray-worker --timeout=300s")
lines.append("```")
lines.append("")
lines.append("## Safety Notes")
lines.append("- This plan is documentation only and must not mutate the live cluster.")
lines.append("- Re-snapshot the Deployments and Service immediately before any approved maintenance window.")
lines.append("")
lines.append("## Pod Snapshot")
lines.append("```text")
lines.extend((tmp / "pods.txt").read_text(errors="ignore").splitlines())
lines.append("```")
lines.append("")
lines.append("## Service Snapshot")
lines.append("```yaml")
lines.extend((tmp / "service.yaml").read_text(errors="ignore").splitlines())
lines.append("```")

print("\n".join(lines))
PY

kubectl -n "${ns}" create configmap change-plan \
  --from-file=plan.md="${plan_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "prepared change-plan ConfigMap"
