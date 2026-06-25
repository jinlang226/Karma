#!/usr/bin/env python3
"""Oracle for ray/scale_workers.

Verifies the head is ready, the worker deployment is both specced and ready at
the target replica count, and Ray reports the expected live node count.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from common.oracle_lib import (  # noqa: E402
    deployment_ready_replicas,
    deployment_spec_replicas,
    ray_node_count_from_head,
)

NAMESPACE = "ray"
HEAD = "ray-head"
WORKER = "ray-worker"
# Param-aware: a workflow can override target_worker_replicas (e.g. a scale
# sweep 1 -> 3 -> 5). Read this stage's live target from the env (default = the
# standalone value 3) so the oracle verifies whatever this stage scaled to,
# not a baked-in count. A non-solving agent still fails — the criterion is
# unchanged, only WHICH count it checks is redirected.
EXPECTED_WORKERS = int(os.environ.get("BENCH_PARAM_TARGET_WORKER_REPLICAS", "3") or "3")

CONNECTIVITY_TOTAL_TIMEOUT_SEC = 60
CONNECTIVITY_ATTEMPT_TIMEOUT_SEC = 12


def check_head() -> int:
    """Confirm the head deployment has at least one ready replica."""
    ready = deployment_ready_replicas(NAMESPACE, HEAD)
    if ready < 1:
        print(f"deployment/{HEAD} ready replicas {ready}, expected at least 1")
        return 1
    print(f"deployment/{HEAD} ready replicas {ready}")
    return 0


def check_worker_spec() -> int:
    """Confirm the worker deployment is specced at the target replica count."""
    replicas = deployment_spec_replicas(NAMESPACE, WORKER)
    if replicas != EXPECTED_WORKERS:
        print(f"deployment/{WORKER} spec replicas {replicas}, expected {EXPECTED_WORKERS}")
        return 1
    print(f"deployment/{WORKER} spec replicas {replicas}")
    return 0


def check_worker_ready() -> int:
    """Confirm the workers are functionally up at the target count.

    O-funcready: a Ray worker registers as a live raylet (and serves tasks)
    before its k8s Deployment readiness probe flips Ready, so a single-snapshot
    ``readyReplicas != EXPECTED_WORKERS`` read false-fails a cluster whose
    workers have already joined the head. Grade the functional signal -- Ray's
    own live-node count (``ray.nodes()`` Alive), polled to convergence -- which
    proves every worker actually joined. Not a loosening: a worker that never
    comes up leaves the live-node count short and still fails. check_worker_spec
    keeps the exact-count (no over-scale) assertion on spec.replicas.
    """
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
            print(f"ray reports {node_count} live nodes ({EXPECTED_WORKERS} workers up)")
            return 0
        time.sleep(3)
    if last_error:
        print(f"ray worker liveness probe failed: {last_error}")
        return 1
    print(f"ray reports {last_count} live nodes, expected at least {expected_nodes}")
    return 1


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
    """Run every scale_workers verification check in order."""
    for fn in (check_head, check_worker_spec, check_worker_ready, check_connectivity):
        rc = fn()
        if rc != 0:
            return rc
    print("ray scale_workers verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
