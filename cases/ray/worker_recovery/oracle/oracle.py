#!/usr/bin/env python3
"""Oracle for ray/worker_recovery.

Verifies the head is ready, the worker deployment recovered to the expected
ready replica count, and Ray reports the expected live node count.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from common.oracle_lib import (  # noqa: E402
    deployment_ready_replicas,
    ray_node_count_from_head,
    resolve_expected_workers,
)

NAMESPACE = "ray"
HEAD = "ray-head"
WORKER = "ray-worker"
# Live/param-aware worker count: recovery restores the cluster to whatever worker
# topology it inherits, it does not redefine it. Resolve param override -> live
# worker spec -> the standalone default 2 so a cluster scaled to N workers must
# recover all N (a dropped/unready worker still fails the check).
EXPECTED_WORKERS = resolve_expected_workers(NAMESPACE, WORKER, default=2)

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


def check_worker_ready() -> int:
    """Confirm the workers functionally recovered to the expected count.

    O-funcready: a recovered Ray worker registers as a live raylet (and serves
    tasks) before its k8s Deployment readiness probe flips Ready, so a
    single-snapshot ``readyReplicas < EXPECTED_WORKERS`` read false-fails a
    cluster whose replacement workers have already rejoined the head. Grade the
    functional signal -- Ray's own live-node count (``ray.nodes()`` Alive),
    polled to convergence -- which proves every worker actually recovered into
    the cluster. Not a loosening: a worker that never recovers leaves the count
    short and still fails.
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
    """Run every worker_recovery verification check in order."""
    for fn in (check_head, check_worker_ready, check_connectivity):
        rc = fn()
        if rc != 0:
            return rc
    print("ray worker_recovery verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
