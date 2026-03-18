#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class RayNames:
    cluster_prefix: str

    @property
    def head(self) -> str:
        return f"{self.cluster_prefix}-head"

    @property
    def worker(self) -> str:
        return f"{self.cluster_prefix}-worker"

    @property
    def client(self) -> str:
        return f"{self.cluster_prefix}-client"

    @property
    def curl_test(self) -> str:
        return f"{self.cluster_prefix}-curl-test"

    @property
    def job_script(self) -> str:
        return f"{self.cluster_prefix}-job-script"

    @property
    def job_runner(self) -> str:
        return f"{self.cluster_prefix}-job-runner"


def bench_namespace(default: str = "ray") -> str:
    return os.environ.get("BENCH_NAMESPACE", default)


def bench_cluster_prefix(default: str = "ray") -> str:
    return os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", default)


def names_from_env(default: str = "ray") -> RayNames:
    return RayNames(cluster_prefix=bench_cluster_prefix(default))


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


def service(namespace: str, name: str) -> dict:
    return kubectl_json(namespace, ["get", "service", name])


def job(namespace: str, name: str) -> dict:
    return kubectl_json(namespace, ["get", "job", name])


def configmap(namespace: str, name: str) -> dict:
    return kubectl_json(namespace, ["get", "configmap", name])


def deployment_ready_replicas(namespace: str, name: str) -> int:
    data = deployment(namespace, name)
    return int(data.get("status", {}).get("readyReplicas", 0) or 0)


def deployment_spec_replicas(namespace: str, name: str) -> int:
    data = deployment(namespace, name)
    return int(data.get("spec", {}).get("replicas", 0) or 0)


def deployment_image(namespace: str, name: str) -> str:
    data = deployment(namespace, name)
    containers = data.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    if not containers:
        raise RuntimeError(f"deployment/{name} has no containers")
    return str(containers[0].get("image") or "")


def service_ports(namespace: str, name: str) -> set[int]:
    data = service(namespace, name)
    ports = set()
    for item in data.get("spec", {}).get("ports", []):
        try:
            ports.add(int(item.get("port")))
        except (TypeError, ValueError):
            continue
    return ports


def configmap_value(namespace: str, name: str, key: str) -> str:
    data = configmap(namespace, name)
    return str((data.get("data", {}) or {}).get(key) or "")


def resource_missing(namespace: str, kind: str, name: str) -> bool:
    proc = run(["kubectl", "-n", namespace, "get", kind, name])
    if proc.returncode == 0:
        return False
    stderr = proc.stderr.lower()
    return "not found" in stderr or "notfound" in stderr


def namespace_exists(name: str) -> bool:
    proc = run(["kubectl", "get", "namespace", name])
    return proc.returncode == 0


def job_succeeded(namespace: str, name: str) -> bool:
    data = job(namespace, name)
    return int(data.get("status", {}).get("succeeded", 0) or 0) >= 1


def job_failed(namespace: str, name: str) -> bool:
    data = job(namespace, name)
    return int(data.get("status", {}).get("failed", 0) or 0) >= 1


def job_logs(namespace: str, name: str) -> str:
    proc = run(["kubectl", "-n", namespace, "logs", f"job/{name}"], check=True)
    return proc.stdout


def ray_node_count_from_head(namespace: str, head_deployment: str) -> int:
    proc = run(
        [
            "kubectl",
            "-n",
            namespace,
            "exec",
            f"deployment/{head_deployment}",
            "--",
            "ray",
            "status",
        ],
        check=True,
    )
    active = False
    count = 0
    for raw_line in proc.stdout.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == "Active:":
            active = True
            continue
        if active and stripped == "Pending:":
            break
        if not active or not stripped or stripped.startswith("("):
            continue
        first = stripped.split()[0]
        try:
            count += int(first)
        except ValueError as exc:
            raise RuntimeError(f"unexpected ray status line: {stripped}") from exc
    if count < 1:
        raise RuntimeError("ray status reported no active nodes")
    return count


def curl_dashboard_status(namespace: str, curl_pod: str, head_service: str, port: int) -> str:
    proc = run(
        [
            "kubectl",
            "-n",
            namespace,
            "exec",
            curl_pod,
            "--",
            "curl",
            "-sS",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            f"http://{head_service}:{port}/api/cluster_status",
        ],
        check=True,
    )
    return proc.stdout.strip()
