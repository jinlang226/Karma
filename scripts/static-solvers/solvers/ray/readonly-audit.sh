#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: ray/readonly-audit
# Strategy: native_shell
# Notes: Read-only auditor. It snapshots the live Ray state into
# ConfigMap/config-audit without mutating the cluster.

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
capture_cmd configmaps.txt kubectl -n "${ns}" get configmap
capture_cmd service.yaml kubectl -n "${ns}" get svc ray-head -o yaml

findings_path="${STATIC_SOLVER_STAGE_DIR}/findings.txt"
python3 - "${tmp_dir}" > "${findings_path}" <<'PY'
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
head_image = str(head.get("image") or "")
worker_image = str(worker.get("image") or "")
ports = service_ports(service)

lines: list[str] = []
lines.append("Ray Read-Only Audit")
lines.append("Namespace: ray")
lines.append("")
lines.append("Current state")
lines.append(f"- worker replicas: {worker_replicas}")
lines.append(f"- ray-head image: {head_image or 'unknown'}")
lines.append(f"- ray-worker image: {worker_image or 'unknown'}")
lines.append(f"- ray-head service ports: {', '.join(str(port) for port in ports) or 'none captured'}")
lines.append("")
lines.append("Baseline comparison")
if worker_replicas != default_workers:
    lines.append(f"- Worker replica count deviates from the baseline of {default_workers}.")
else:
    lines.append(f"- Worker replica count matches the baseline of {default_workers}.")
if head_image != default_image or worker_image != default_image:
    lines.append(f"- One or more deployment images differ from the baseline image {default_image}.")
else:
    lines.append(f"- Both deployments match the baseline image {default_image}.")
extra_ports = [port for port in ports if port not in default_service_ports]
if extra_ports:
    lines.append(f"- Non-default service ports detected on ray-head: {', '.join(str(port) for port in extra_ports)}.")
else:
    lines.append("- No non-default service ports were captured on ray-head.")
lines.append("")
lines.append("Compliance notes")
lines.append("- This audit is read-only; no deployment, service, or ConfigMap changes were applied.")
lines.append("- Review whether the current worker count, service exposure, and image tags match the intended workflow state before approving any follow-up change window.")
lines.append("")
lines.append("Pod snapshot")
lines.extend((tmp / "pods.txt").read_text(errors="ignore").splitlines())
lines.append("")
lines.append("ConfigMap inventory")
lines.extend((tmp / "configmaps.txt").read_text(errors="ignore").splitlines())
lines.append("")
lines.append("Service snapshot")
lines.extend((tmp / "service.yaml").read_text(errors="ignore").splitlines())

print("\n".join(lines))
PY

kubectl -n "${ns}" create configmap config-audit \
  --from-file=findings.txt="${findings_path}" \
  --dry-run=client -o yaml | kubectl -n "${ns}" apply -f -

static_solver_write_submit "prepared config-audit ConfigMap"
