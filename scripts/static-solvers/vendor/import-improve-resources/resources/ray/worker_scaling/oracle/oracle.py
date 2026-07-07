#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    deployment_ready_replicas,
    deployment_spec_replicas,
    names_from_env,
    ray_node_count_from_head,
)

CONNECTIVITY_TOTAL_TIMEOUT_SEC = 60
CONNECTIVITY_ATTEMPT_TIMEOUT_SEC = 12


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        required=True,
        choices=["head", "worker-spec", "worker-ready", "connectivity"],
    )
    parser.add_argument("--expected-workers", type=int, required=True)
    args = parser.parse_args()

    ns = bench_namespace()
    names = names_from_env()

    if args.check == "head":
        ready = deployment_ready_replicas(ns, names.head)
        if ready < 1:
            print(f"deployment/{names.head} ready replicas {ready}, expected at least 1")
            return 1
        print(f"deployment/{names.head} ready replicas {ready}")
        return 0

    if args.check == "worker-spec":
        replicas = deployment_spec_replicas(ns, names.worker)
        if replicas != args.expected_workers:
            print(f"deployment/{names.worker} spec replicas {replicas}, expected {args.expected_workers}")
            return 1
        print(f"deployment/{names.worker} spec replicas {replicas}")
        return 0

    if args.check == "worker-ready":
        ready = deployment_ready_replicas(ns, names.worker)
        if ready != args.expected_workers:
            print(f"deployment/{names.worker} ready replicas {ready}, expected {args.expected_workers}")
            return 1
        print(f"deployment/{names.worker} ready replicas {ready}")
        return 0

    expected_nodes = 1 + args.expected_workers
    deadline = time.time() + CONNECTIVITY_TOTAL_TIMEOUT_SEC
    last_count = 0
    last_error = ""
    while time.time() < deadline:
        try:
            node_count = ray_node_count_from_head(
                ns,
                names.head,
                timeout_sec=CONNECTIVITY_ATTEMPT_TIMEOUT_SEC,
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


if __name__ == "__main__":
    raise SystemExit(main())
