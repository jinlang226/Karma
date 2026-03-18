#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import subprocess


def bench_namespace(default: str = "nginx-app") -> str:
    return os.environ.get("BENCH_NAMESPACE", default)


def bench_ns(role: str, default: str | None = None) -> str:
    key = "BENCH_NS_" + str(role or "").upper().replace("-", "_")
    return os.environ.get(key, default or "")


def bench_param(name: str, default: str = "") -> str:
    key = "BENCH_PARAM_" + str(name or "").upper().replace("-", "_")
    return os.environ.get(key, default)


def run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "command failed")
    return proc


def kubectl_json(args: list[str], *, namespace: str | None = None) -> dict:
    cmd = ["kubectl"]
    if namespace:
        cmd.extend(["-n", namespace])
    cmd.extend(args)
    cmd.extend(["-o", "json"])
    proc = run(cmd, check=True)
    return json.loads(proc.stdout)


def deployment(namespace: str, name: str) -> dict:
    return kubectl_json(["get", "deployment", name], namespace=namespace)


def service(namespace: str, name: str) -> dict:
    return kubectl_json(["get", "service", name], namespace=namespace)


def ingress(namespace: str, name: str) -> dict:
    return kubectl_json(["get", "ingress", name], namespace=namespace)


def configmap(namespace: str, name: str) -> dict:
    return kubectl_json(["get", "configmap", name], namespace=namespace)


def secret(namespace: str, name: str) -> dict:
    return kubectl_json(["get", "secret", name], namespace=namespace)


def deployment_ready_replicas(namespace: str, name: str) -> int:
    payload = deployment(namespace, name)
    return int(payload.get("status", {}).get("readyReplicas", 0) or 0)


def deployment_spec_replicas(namespace: str, name: str) -> int:
    payload = deployment(namespace, name)
    return int(payload.get("spec", {}).get("replicas", 0) or 0)


def deployment_args(namespace: str, name: str) -> list[str]:
    payload = deployment(namespace, name)
    containers = payload.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []) or []
    if not containers:
        return []
    return [str(item) for item in (containers[0].get("args") or [])]


def service_ports(namespace: str, name: str) -> set[int]:
    payload = service(namespace, name)
    ports: set[int] = set()
    for item in payload.get("spec", {}).get("ports", []) or []:
        try:
            ports.add(int(item.get("port")))
        except (TypeError, ValueError):
            continue
    return ports


def service_cluster_ip(namespace: str, name: str) -> str:
    payload = service(namespace, name)
    return str(payload.get("spec", {}).get("clusterIP") or "")


def configmap_data(namespace: str, name: str) -> dict[str, str]:
    payload = configmap(namespace, name)
    raw = payload.get("data", {}) or {}
    return {str(k): str(v) for k, v in raw.items()}


def ingress_annotations(namespace: str, name: str) -> dict[str, str]:
    payload = ingress(namespace, name)
    raw = payload.get("metadata", {}).get("annotations", {}) or {}
    return {str(k): str(v) for k, v in raw.items()}


def ingress_class_name(namespace: str, name: str) -> str:
    payload = ingress(namespace, name)
    return str(payload.get("spec", {}).get("ingressClassName") or "")


def ingress_paths(namespace: str, name: str, *, host: str | None = None) -> list[tuple[str, str, str]]:
    payload = ingress(namespace, name)
    items: list[tuple[str, str, str]] = []
    for rule in payload.get("spec", {}).get("rules", []) or []:
        rule_host = str(rule.get("host") or "")
        if host and rule_host != host:
            continue
        http = rule.get("http", {}) or {}
        for path_item in http.get("paths", []) or []:
            backend = path_item.get("backend", {}).get("service", {}) or {}
            items.append(
                (
                    rule_host,
                    str(path_item.get("path") or ""),
                    str(backend.get("name") or ""),
                )
            )
    return items


def ingress_tls_secret(namespace: str, name: str, *, host: str | None = None) -> str:
    payload = ingress(namespace, name)
    for item in payload.get("spec", {}).get("tls", []) or []:
        hosts = [str(entry) for entry in (item.get("hosts") or [])]
        if host and host not in hosts:
            continue
        return str(item.get("secretName") or "")
    return ""


def secret_data_text(namespace: str, name: str, key: str) -> str:
    payload = secret(namespace, name)
    encoded = str((payload.get("data", {}) or {}).get(key) or "")
    if not encoded:
        return ""
    return base64.b64decode(encoded).decode("utf-8")


def controller_service_host(namespace: str) -> str:
    return f"ingress-nginx-controller.{namespace}.svc.cluster.local"


def pod_exec(namespace: str, pod: str, argv: list[str]) -> str:
    proc = run(["kubectl", "-n", namespace, "exec", pod, "--", *argv], check=True)
    return proc.stdout
