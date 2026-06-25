#!/usr/bin/env python3
import os
import re
import sys
from pathlib import Path
from typing import List, Optional

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from solver_utils import kubectl_json, run, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")
FROM_VERSION = os.environ.get("BENCH_PARAM_FROM_VERSION", "3.9")
TO_VERSION = os.environ.get("BENCH_PARAM_TO_VERSION", "4.1")


def version_matches_series(version: str, requested: str) -> bool:
    requested = requested.strip()
    family_prefix = requested if requested.endswith(".") else f"{requested}."
    return version == requested or version.startswith(family_prefix)


def rabbitmqctl(pod: str, *args: str) -> str:
    return run(["kubectl", "-n", NAMESPACE, "exec", pod, "--", "rabbitmqctl", *args])


def list_ready_pods():
    payload = kubectl_json("-n", NAMESPACE, "get", "pods", "-l", f"app={CLUSTER_PREFIX}")
    ready = []
    for item in payload.get("items", []):
        name = item.get("metadata", {}).get("name", "")
        phase = item.get("status", {}).get("phase")
        statuses = item.get("status", {}).get("containerStatuses", [])
        if phase == "Running" and statuses and all(s.get("ready") for s in statuses):
            ready.append(name)
    return sorted(ready)


def get_pod_version(pod: str) -> Optional[str]:
    out = rabbitmqctl(pod, "status")
    match = re.search(r"RabbitMQ\s*version\s*[:=]\s*([0-9]+\.[0-9]+\.[0-9]+)", out)
    if not match:
        match = re.search(r'"RabbitMQ"\s*,\s*"([0-9]+\.[0-9]+\.[0-9]+)"', out)
    if not match:
        return None
    return match.group(1)


def versions_match_target(requested: str):
    for pod in (f"{CLUSTER_PREFIX}-0", f"{CLUSTER_PREFIX}-1", f"{CLUSTER_PREFIX}-2"):
        version = get_pod_version(pod)
        if not version:
            return False
        if not version_matches_series(version, requested):
            return False
    return True


def cluster_has_three_running_nodes() -> bool:
    ready = list_ready_pods()
    if len(ready) < 3:
        return False
    out = rabbitmqctl(ready[0], "cluster_status")
    running = set(re.findall(r"rabbit@[^\s,\]\}]+", out))
    return len(running) >= 3


def seeded_queue_present() -> bool:
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


def wait_cluster_healthy(version: str, timeout_sec: int = 1200) -> None:
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
    ready = list_ready_pods()
    if not ready:
        raise RuntimeError("no ready RabbitMQ pod available to enable feature flags")
    rabbitmqctl(ready[0], "enable_feature_flag", "all")


def version_series(version: str) -> str:
    parts = version.strip().split(".")
    if len(parts) < 2:
        raise RuntimeError(f"unsupported RabbitMQ version format: {version}")
    return f"{parts[0]}.{parts[1]}"


def build_upgrade_path(from_version: str, to_version: str) -> List[str]:
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
        wait_cluster_healthy(version, timeout_sec=1800)
        enable_all_feature_flags()
        wait_cluster_healthy(version, timeout_sec=900)
    print("manual_skip_upgrade solver applied")


if __name__ == "__main__":
    main()
