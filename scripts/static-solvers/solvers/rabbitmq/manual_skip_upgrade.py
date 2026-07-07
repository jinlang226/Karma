#!/usr/bin/env python3
"""Upgrade RabbitMQ across supported hops with proxy-resilient kubectl access."""

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")
FROM_VERSION = os.environ.get("BENCH_PARAM_FROM_VERSION", "3.9")
TO_VERSION = os.environ.get("BENCH_PARAM_TO_VERSION", "4.1")
HOME_KUBECONFIG = Path.home() / ".kube" / "config"
USE_DIRECT_KUBECONFIG = False


def _subprocess_env():
    """Return the kubectl environment, optionally bypassing the stage proxy."""
    env = os.environ.copy()
    if USE_DIRECT_KUBECONFIG and HOME_KUBECONFIG.exists():
        env["KUBECONFIG"] = str(HOME_KUBECONFIG)
    return env


def _proxy_refused(detail: str) -> bool:
    """Return whether a kubectl failure came from the dead local proxy."""
    lowered = detail.lower()
    return "connection refused" in lowered and "127.0.0.1" in detail


def run(cmd, input_text=None):
    """Run one command, switching to host kubeconfig on proxy refusal once."""
    global USE_DIRECT_KUBECONFIG
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        env=_subprocess_env(),
        check=False,
    )
    if proc.returncode == 0:
        return proc.stdout
    stderr = (proc.stderr or "").strip()
    stdout = (proc.stdout or "").strip()
    detail = stderr or stdout or f"exit={proc.returncode}"
    if (not USE_DIRECT_KUBECONFIG) and HOME_KUBECONFIG.exists() and _proxy_refused(detail):
        print(
            "[static-solver] kube-proxy refused connection; retrying with host kubeconfig",
            file=sys.stderr,
        )
        USE_DIRECT_KUBECONFIG = True
        proc = subprocess.run(
            cmd,
            input=input_text,
            text=True,
            capture_output=True,
            env=_subprocess_env(),
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        detail = stderr or stdout or f"exit={proc.returncode}"
    raise RuntimeError(f"command failed: {' '.join(str(c) for c in cmd)} :: {detail}")


def run_json(cmd):
    """Run one JSON-emitting command and decode it."""
    import json

    return json.loads(run(cmd) or "{}")


def kubectl_json(*args):
    """Run kubectl with JSON output."""
    return run_json(["kubectl", *args, "-o", "json"])


def wait_until(predicate, timeout_sec=120, interval_sec=2, description="condition"):
    """Wait for one predicate to become true."""
    end = time.time() + timeout_sec
    last_error = None
    while time.time() < end:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(interval_sec)
    if last_error:
        raise RuntimeError(f"timeout waiting for {description}: {last_error}")
    raise RuntimeError(f"timeout waiting for {description}")


def rabbitmqctl(pod: str, *args: str) -> str:
    """Run rabbitmqctl inside one broker pod."""
    return run(["kubectl", "-n", NAMESPACE, "exec", pod, "--", "rabbitmqctl", *args])


def list_ready_pods():
    """Return ready RabbitMQ broker pod names."""
    payload = kubectl_json("-n", NAMESPACE, "get", "pods", "-l", f"app={CLUSTER_PREFIX}")
    ready = []
    for item in payload.get("items", []):
        name = item.get("metadata", {}).get("name", "")
        phase = item.get("status", {}).get("phase")
        statuses = item.get("status", {}).get("containerStatuses", [])
        if phase == "Running" and statuses and all(status.get("ready") for status in statuses):
            ready.append(name)
    return sorted(ready)


def deployment_exists(name: str) -> bool:
    """Return whether one helper deployment exists."""
    try:
        run(["kubectl", "-n", NAMESPACE, "get", f"deployment/{name}"])
    except RuntimeError as exc:
        if "NotFound" in str(exc):
            return False
        raise
    return True


def scale_deployment(name: str, replicas: int) -> None:
    """Scale one deployment to the requested replica count."""
    run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "scale",
            f"deployment/{name}",
            f"--replicas={replicas}",
        ]
    )


def deployment_scaled_down(name: str) -> bool:
    """Return whether a deployment has no desired or ready replicas left."""
    payload = kubectl_json("-n", NAMESPACE, "get", "deployment", name)
    spec = payload.get("spec") or {}
    status = payload.get("status") or {}
    return int(spec.get("replicas") or 0) == 0 and int(status.get("readyReplicas") or 0) == 0


def quiesce_inherited_helpers() -> None:
    """Scale down inherited helper deployments that are not part of this case."""
    helpers = [name for name in ("app-producer", "app-client", "ops-client") if deployment_exists(name)]
    for name in helpers:
        scale_deployment(name, 0)
    for name in helpers:
        wait_until(
            lambda deployment=name: deployment_scaled_down(deployment),
            timeout_sec=180,
            interval_sec=5,
            description=f"deployment/{name} to scale down",
        )


def get_pod_version(pod: str) -> Optional[str]:
    """Read the running RabbitMQ version from one broker pod."""
    out = rabbitmqctl(pod, "status")
    match = re.search(r"RabbitMQ\s*version\s*[:=]\s*([0-9]+\.[0-9]+\.[0-9]+)", out)
    if not match:
        match = re.search(r'"RabbitMQ"\s*,\s*"([0-9]+\.[0-9]+\.[0-9]+)"', out)
    if not match:
        return None
    return match.group(1)


def version_matches_series(version: str, requested: str) -> bool:
    """Return whether a version is exact or within the requested minor family."""
    requested = requested.strip()
    family_prefix = requested if requested.endswith(".") else f"{requested}."
    return version == requested or version.startswith(family_prefix)


def versions_match_target(requested: str) -> bool:
    """Return whether all three brokers run the requested target family."""
    for pod in (f"{CLUSTER_PREFIX}-0", f"{CLUSTER_PREFIX}-1", f"{CLUSTER_PREFIX}-2"):
        version = get_pod_version(pod)
        if not version or not version_matches_series(version, requested):
            return False
    return True


def cluster_has_three_running_nodes() -> bool:
    """Return whether the cluster reports all three brokers as running nodes."""
    ready = list_ready_pods()
    if len(ready) < 3:
        return False
    out = rabbitmqctl(ready[0], "cluster_status")
    running = set(re.findall(r"rabbit@[^\s,\]\}]+", out))
    return len(running) >= 3


def seeded_queue_present() -> bool:
    """Return whether /app/app-queue exists and still has messages."""
    ready = list_ready_pods()
    if not ready:
        return False
    out = rabbitmqctl(ready[0], "-q", "list_queues", "-p", "/app", "name", "messages")
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == "app-queue":
            try:
                return int(parts[1]) >= 1
            except ValueError:
                return False
    return False


def wait_cluster_healthy(version: str, timeout_sec: int = 600) -> None:
    """Wait until the cluster is healthy on the requested target family."""
    wait_until(
        lambda: len(list_ready_pods()) == 3
        and versions_match_target(version)
        and cluster_has_three_running_nodes()
        and seeded_queue_present(),
        timeout_sec=timeout_sec,
        interval_sec=10,
        description=f"RabbitMQ cluster healthy on {version}.x or exact {version}",
    )


def enable_all_feature_flags() -> None:
    """Enable all RabbitMQ feature flags on one ready broker."""
    ready = list_ready_pods()
    if not ready:
        raise RuntimeError("no ready RabbitMQ pod available to enable feature flags")
    rabbitmqctl(ready[0], "enable_feature_flag", "all")


def version_series(version: str) -> str:
    """Return the major.minor portion of a version string."""
    parts = version.strip().split(".")
    if len(parts) < 2:
        raise RuntimeError(f"unsupported RabbitMQ version format: {version}")
    return f"{parts[0]}.{parts[1]}"


def build_upgrade_path(from_version: str, to_version: str) -> List[str]:
    """Return the supported hop-by-hop upgrade path."""
    start = version_series(from_version)
    target = version_series(to_version)
    if start == target:
        return [target]
    next_hops = {
        "3.9": ["3.10"],
        "3.10": ["3.11"],
        "3.11": ["3.12"],
        "3.12": ["3.13"],
        "3.13": ["4.0", "4.1", "4.2"],
        "4.0": ["4.1", "4.2"],
        "4.1": ["4.2"],
    }
    frontier = [(start, [])]
    seen = {start}
    while frontier:
        current, path = frontier.pop(0)
        for nxt in next_hops.get(current, []):
            if nxt in seen:
                continue
            next_path = [*path, nxt]
            if nxt == target:
                return next_path
            seen.add(nxt)
            frontier.append((nxt, next_path))
    raise RuntimeError(f"no supported RabbitMQ upgrade path from {start} to {target}")


def main():
    """Quiesce inherited helpers, then upgrade hop by hop."""
    quiesce_inherited_helpers()
    for version in build_upgrade_path(FROM_VERSION, TO_VERSION):
        target_image = f"rabbitmq:{version}-management"
        run(
            [
                "kubectl",
                "-n",
                NAMESPACE,
                "set",
                "image",
                f"statefulset/{CLUSTER_PREFIX}",
                f"rabbitmq={target_image}",
            ]
        )
        wait_cluster_healthy(version, timeout_sec=600)
        enable_all_feature_flags()
        wait_cluster_healthy(version, timeout_sec=300)
    print("manual_skip_upgrade solver applied")


if __name__ == "__main__":
    main()
