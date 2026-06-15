#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
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


def pods(namespace: str, selector: str) -> dict:
    return kubectl_json(namespace, ["get", "pods", "-l", selector])


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


def service_cluster_ip(namespace: str, name: str) -> str:
    data = service(namespace, name)
    cluster_ip = str(data.get("spec", {}).get("clusterIP") or "").strip()
    if not cluster_ip or cluster_ip.lower() == "none":
        raise RuntimeError(f"service/{name} has no routable cluster IP")
    return cluster_ip


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


def ray_node_count_from_head(namespace: str, head_deployment: str, timeout_sec: float = 10.0) -> int:
    selector = deployment(namespace, head_deployment).get("spec", {}).get("selector", {}).get("matchLabels", {})
    if not selector:
        raise RuntimeError(f"deployment/{head_deployment} has no selector labels")
    selector_text = ",".join(f"{key}={value}" for key, value in selector.items())
    pod_items = (pods(namespace, selector_text).get("items", []) or [])
    if not pod_items:
        raise RuntimeError(f"deployment/{head_deployment} has no pods")
    pod_name = str(pod_items[0].get("metadata", {}).get("name") or "").strip()
    if not pod_name:
        raise RuntimeError(f"deployment/{head_deployment} pod is missing metadata.name")
    # Pin the driver's node IP to the head pod's own IP (exported as MY_POD_IP by
    # the head manifest). Without this, ray.init(address='auto') auto-detects an
    # address that may not match any registered raylet on single-host clusters
    # (kind), failing with "none of these match this node's IP".
    script = (
        "import os, ray; "
        "ray.init(address='auto', ignore_reinit_error=True, "
        "_node_ip_address=os.environ.get('MY_POD_IP') or None); "
        "print(sum(1 for node in ray.nodes() if node.get('Alive')))"
    )
    # Keep this helper to a single bounded probe window. The caller should own
    # any larger retry budget so command-level timeouts remain easy to reason about.
    try:
        timeout_budget = float(timeout_sec)
    except (TypeError, ValueError):
        timeout_budget = 10.0
    if timeout_budget <= 0:
        timeout_budget = 10.0
    deadline = time.time() + timeout_budget
    last_error: str | None = None
    while True:
        proc = run(
            [
                "kubectl",
                "-n",
                namespace,
                "exec",
                pod_name,
                "--",
                "python",
                "-c",
                script,
            ]
        )
        if proc.returncode == 0:
            count = 0
            for raw_line in proc.stdout.splitlines():
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    count = int(stripped)
                except ValueError:
                    continue
                else:
                    break
            if count >= 1:
                return count
            last_error = f"ray node probe returned no active nodes: {proc.stdout.strip()}"
        else:
            last_error = proc.stderr.strip() or proc.stdout.strip() or "command failed"
        if time.time() >= deadline:
            break
        time.sleep(min(2.0, max(0.0, deadline - time.time())))
    raise RuntimeError(last_error or "ray node probe timed out")


def resolve_expected_workers(
    namespace: str,
    worker_deployment: str,
    *,
    default: int = 2,
    param_env: tuple[str, ...] = ("BENCH_PARAM_EXPECTED_WORKERS", "BENCH_PARAM_WORKER_REPLICAS"),
) -> int:
    """Resolve the worker count this oracle should expect.

    Priority (Transform 2): explicit param override (BENCH_PARAM_EXPECTED_WORKERS
    / BENCH_PARAM_WORKER_REPLICAS) -> the LIVE worker count inherited from the
    cluster (the worker Deployment's spec.replicas) -> the old hardcoded default.

    Stages that do NOT themselves change the worker count (e.g. an image upgrade
    or a pod-recovery rehearsal) must adapt to whatever topology they inherit:
    if a prior workflow stage scaled the cluster to N workers, the oracle should
    still require all N to be live rather than a baked-in 2. The check itself is
    unchanged — fewer-than-expected ready workers / dropped nodes still fail.
    """
    for env_name in param_env:
        raw = os.environ.get(env_name)
        if raw is None or not str(raw).strip():
            continue
        try:
            return int(str(raw).strip())
        except ValueError:
            continue
    try:
        live = deployment_spec_replicas(namespace, worker_deployment)
    except Exception:  # noqa: BLE001
        live = 0
    if live >= 1:
        return live
    return default


def curl_dashboard_status(namespace: str, curl_pod: str, head_service: str, port: int) -> str:
    cluster_ip = service_cluster_ip(namespace, head_service)
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
            f"http://{cluster_ip}:{port}/api/cluster_status",
        ],
        check=True,
    )
    return proc.stdout.strip()
