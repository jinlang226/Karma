#!/usr/bin/env python3
"""Oracle for ray/upgrade_version.

Verifies both the head and worker deployments run the target Ray image, remain
ready, and that Ray still reports the expected live node count.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from common.oracle_lib import (  # noqa: E402
    deployment_image,
    deployment_ready_replicas,
    ray_node_count_from_head,
)

NAMESPACE = "ray"
HEAD = "ray-head"
WORKER = "ray-worker"
# Param-aware: a workflow can override to_image (the target upgrade image).
# Read it from the env (default = the standalone value) so the oracle verifies
# whatever version this stage upgraded to, not a baked-in tag.
EXPECTED_IMAGE = os.environ.get("BENCH_PARAM_TO_IMAGE", "rayproject/ray:2.9.0") or "rayproject/ray:2.9.0"
EXPECTED_WORKERS = 2

CONNECTIVITY_TOTAL_TIMEOUT_SEC = 60
CONNECTIVITY_ATTEMPT_TIMEOUT_SEC = 12


def check_head_image() -> int:
    """Confirm the head deployment runs the target image."""
    image = deployment_image(NAMESPACE, HEAD)
    if image != EXPECTED_IMAGE:
        print(f"deployment/{HEAD} image {image}, expected {EXPECTED_IMAGE}")
        return 1
    print(f"deployment/{HEAD} image {image}")
    return 0


def check_worker_image() -> int:
    """Confirm the worker deployment runs the target image."""
    image = deployment_image(NAMESPACE, WORKER)
    if image != EXPECTED_IMAGE:
        print(f"deployment/{WORKER} image {image}, expected {EXPECTED_IMAGE}")
        return 1
    print(f"deployment/{WORKER} image {image}")
    return 0


def check_head_ready() -> int:
    """Confirm the head deployment has at least one ready replica."""
    ready = deployment_ready_replicas(NAMESPACE, HEAD)
    if ready < 1:
        print(f"deployment/{HEAD} ready replicas {ready}, expected at least 1")
        return 1
    print(f"deployment/{HEAD} ready replicas {ready}")
    return 0


def check_worker_ready() -> int:
    """Confirm the worker deployment has the expected ready replicas."""
    ready = deployment_ready_replicas(NAMESPACE, WORKER)
    if ready < EXPECTED_WORKERS:
        print(f"deployment/{WORKER} ready replicas {ready}, expected at least {EXPECTED_WORKERS}")
        return 1
    print(f"deployment/{WORKER} ready replicas {ready}")
    return 0


def check_connectivity() -> int:
    """Confirm Ray reports at least 1 head + EXPECTED_WORKERS live nodes."""
    expected_nodes = 1 + EXPECTED_WORKERS
    deadline = time.time() + CONNECTIVITY_TOTAL_TIMEOUT_SEC
    last_count = 0
    last_error = ""
    while time.time() < deadline:
        try:
            node_count = ray_node_count_from_head(
                NAMESPACE, HEAD, timeout_sec=CONNECTIVITY_ATTEMPT_TIMEOUT_SEC
            )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(3)
            continue
        last_count = node_count
        if node_count >= expected_nodes:
            print(f"ray reports {node_count} nodes")
            return 0
        time.sleep(3)
    if last_error:
        print(f"ray connectivity probe failed: {last_error}")
        return 1
    print(f"ray reports {last_count} nodes, expected at least {expected_nodes}")
    return 1


def main() -> int:
    """Run every upgrade_version verification check in order."""
    for fn in (
        check_head_image,
        check_worker_image,
        check_head_ready,
        check_worker_ready,
        check_connectivity,
    ):
        rc = fn()
        if rc != 0:
            return rc
    print("ray upgrade_version verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
