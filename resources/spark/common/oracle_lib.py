#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import subprocess


def bench_namespace(default: str = "spark") -> str:
    return os.environ.get("BENCH_NAMESPACE", default)


def bench_ns(role: str, default: str | None = None) -> str:
    key = "BENCH_NS_" + str(role or "").upper().replace("-", "_")
    return os.environ.get(key, default or "")


def bench_param(name: str, default: str = "") -> str:
    key = "BENCH_PARAM_" + str(name or "").upper().replace("-", "_")
    return os.environ.get(key, default)


def bench_param_int(name: str, default: int) -> int:
    raw = bench_param(name, str(default))
    try:
        return int(raw)
    except Exception:
        return int(default)


def run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "command failed")
    return proc


def kubectl_json(namespace: str, args: list[str]) -> dict:
    proc = run(["kubectl", "-n", namespace, *args, "-o", "json"], check=True)
    return json.loads(proc.stdout)


def deployment(namespace: str, name: str) -> dict:
    return kubectl_json(namespace, ["get", "deployment", name])


def job(namespace: str, name: str) -> dict:
    return kubectl_json(namespace, ["get", "job", name])


def service(namespace: str, name: str) -> dict:
    return kubectl_json(namespace, ["get", "service", name])


def configmap(namespace: str, name: str) -> dict:
    return kubectl_json(namespace, ["get", "configmap", name])


def secret(namespace: str, name: str) -> dict:
    return kubectl_json(namespace, ["get", "secret", name])


def pvc(namespace: str, name: str) -> dict:
    return kubectl_json(namespace, ["get", "pvc", name])


def deployment_ready_replicas(namespace: str, name: str) -> int:
    data = deployment(namespace, name)
    return int(data.get("status", {}).get("readyReplicas", 0) or 0)


def deployment_spec_replicas(namespace: str, name: str) -> int:
    data = deployment(namespace, name)
    return int(data.get("spec", {}).get("replicas", 0) or 0)


def deployment_env(namespace: str, name: str, env_name: str) -> str:
    data = deployment(namespace, name)
    containers = data.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []) or []
    if not containers:
        return ""
    for env_item in containers[0].get("env", []) or []:
        if env_item.get("name") == env_name:
            return str(env_item.get("value") or "")
    return ""


def deployment_mount_path(namespace: str, name: str, volume_name: str) -> str:
    data = deployment(namespace, name)
    containers = data.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []) or []
    if not containers:
        return ""
    for mount in containers[0].get("volumeMounts", []) or []:
        if mount.get("name") == volume_name:
            return str(mount.get("mountPath") or "")
    return ""


def deployment_pvc_claim(namespace: str, name: str, volume_name: str) -> str:
    data = deployment(namespace, name)
    volumes = data.get("spec", {}).get("template", {}).get("spec", {}).get("volumes", []) or []
    for volume in volumes:
        if volume.get("name") == volume_name:
            pvc_ref = volume.get("persistentVolumeClaim") or {}
            return str(pvc_ref.get("claimName") or "")
    return ""


def deployment_service_account(namespace: str, name: str) -> str:
    data = deployment(namespace, name)
    spec = data.get("spec", {}).get("template", {}).get("spec", {}) or {}
    return str(spec.get("serviceAccountName") or "")


def deployment_container_image(namespace: str, name: str) -> str:
    data = deployment(namespace, name)
    containers = data.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []) or []
    if not containers:
        return ""
    return str(containers[0].get("image") or "")


def job_succeeded(namespace: str, name: str) -> bool:
    data = job(namespace, name)
    return int(data.get("status", {}).get("succeeded", 0) or 0) >= 1


def job_logs(namespace: str, name: str) -> str:
    proc = run(["kubectl", "-n", namespace, "logs", f"job/{name}"], check=True)
    return proc.stdout


def job_active(namespace: str, name: str) -> bool:
    data = job(namespace, name)
    return int(data.get("status", {}).get("active", 0) or 0) >= 1


def job_service_account(namespace: str, name: str) -> str:
    data = job(namespace, name)
    spec = data.get("spec", {}).get("template", {}).get("spec", {}) or {}
    return str(spec.get("serviceAccountName") or "")


def job_container_image(namespace: str, name: str) -> str:
    data = job(namespace, name)
    containers = data.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []) or []
    if not containers:
        return ""
    return str(containers[0].get("image") or "")


def job_env(namespace: str, name: str, env_name: str) -> str:
    data = job(namespace, name)
    containers = data.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []) or []
    if not containers:
        return ""
    for env_item in containers[0].get("env", []) or []:
        if env_item.get("name") == env_name:
            return str(env_item.get("value") or "")
    return ""


def job_mount_path(namespace: str, name: str, volume_name: str) -> str:
    data = job(namespace, name)
    containers = data.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []) or []
    if not containers:
        return ""
    for mount in containers[0].get("volumeMounts", []) or []:
        if mount.get("name") == volume_name:
            return str(mount.get("mountPath") or "")
    return ""


def job_pvc_claim(namespace: str, name: str, volume_name: str) -> str:
    data = job(namespace, name)
    volumes = data.get("spec", {}).get("template", {}).get("spec", {}).get("volumes", []) or []
    for volume in volumes:
        if volume.get("name") == volume_name:
            pvc_ref = volume.get("persistentVolumeClaim") or {}
            return str(pvc_ref.get("claimName") or "")
    return ""


def service_ports(namespace: str, name: str) -> set[int]:
    data = service(namespace, name)
    ports = set()
    for item in data.get("spec", {}).get("ports", []) or []:
        try:
            ports.add(int(item.get("port")))
        except (TypeError, ValueError):
            continue
    return ports


def configmap_value(namespace: str, name: str, key: str) -> str:
    data = configmap(namespace, name)
    return str((data.get("data", {}) or {}).get(key) or "")


def secret_value(namespace: str, name: str, key: str) -> str:
    data = secret(namespace, name)
    encoded = str((data.get("data", {}) or {}).get(key) or "")
    if not encoded:
        return ""
    return base64.b64decode(encoded).decode("utf-8")


def pvc_exists(namespace: str, name: str) -> bool:
    try:
        pvc(namespace, name)
        return True
    except Exception:
        return False
