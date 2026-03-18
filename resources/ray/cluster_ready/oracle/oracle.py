#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    deployment_ready_replicas,
    names_from_env,
    ray_node_count_from_head,
    service_ports,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        required=True,
        choices=["service", "head", "workers", "connectivity"],
    )
    parser.add_argument("--expected-workers", type=int, required=True)
    args = parser.parse_args()

    ns = bench_namespace()
    names = names_from_env()
    if args.check == "service":
        ports = service_ports(ns, names.head)
        if 6379 not in ports:
            print(f"service/{names.head} does not expose port 6379")
            return 1
        print(f"service/{names.head} exposes port 6379")
        return 0

    if args.check == "head":
        ready = deployment_ready_replicas(ns, names.head)
        if ready < 1:
            print(f"deployment/{names.head} ready replicas {ready}, expected at least 1")
            return 1
        print(f"deployment/{names.head} ready replicas {ready}")
        return 0

    if args.check == "workers":
        ready = deployment_ready_replicas(ns, names.worker)
        if ready < args.expected_workers:
            print(
                f"deployment/{names.worker} ready replicas {ready}, expected at least {args.expected_workers}"
            )
            return 1
        print(f"deployment/{names.worker} ready replicas {ready}")
        return 0

    try:
        node_count = ray_node_count_from_head(ns, names.head)
    except Exception as exc:  # noqa: BLE001
        print(f"ray connectivity probe failed: {exc}")
        return 1

    expected_nodes = 1 + args.expected_workers
    if node_count < expected_nodes:
        print(f"ray reports {node_count} nodes, expected at least {expected_nodes}")
        return 1
    print(f"ray reports {node_count} nodes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
