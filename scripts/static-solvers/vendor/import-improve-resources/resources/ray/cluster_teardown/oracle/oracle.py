#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import bench_namespace, names_from_env, namespace_exists, resource_missing  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        required=True,
        choices=["namespace", "head-missing", "worker-missing", "service-missing"],
    )
    args = parser.parse_args()

    ns = bench_namespace()
    names = names_from_env()

    if args.check == "namespace":
        if not namespace_exists(ns):
            print(f"namespace/{ns} is missing")
            return 1
        print(f"namespace/{ns} still exists")
        return 0

    if args.check == "head-missing":
        if not resource_missing(ns, "deployment", names.head):
            print(f"deployment/{names.head} still exists")
            return 1
        print(f"deployment/{names.head} is absent")
        return 0

    if args.check == "worker-missing":
        if not resource_missing(ns, "deployment", names.worker):
            print(f"deployment/{names.worker} still exists")
            return 1
        print(f"deployment/{names.worker} is absent")
        return 0

    if not resource_missing(ns, "service", names.head):
        print(f"service/{names.head} still exists")
        return 1
    print(f"service/{names.head} is absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
