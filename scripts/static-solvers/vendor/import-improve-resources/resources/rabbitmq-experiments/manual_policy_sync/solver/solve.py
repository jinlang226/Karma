#!/usr/bin/env python3
import os
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from solver_utils import run, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")
SEED_POD = f"{CLUSTER_PREFIX}-0"


def policy_applied():
    out = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            SEED_POD,
            "--",
            "rabbitmqctl",
            "-q",
            "list_queues",
            "-p",
            "/app",
            "name",
            "type",
            "policy",
        ]
    )
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0] == "app-queue":
            return parts[1] == "classic" and parts[2] == "ha-all"
    return False


def main():
    run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            SEED_POD,
            "--",
            "rabbitmqctl",
            "set_policy",
            "-p",
            "/app",
            "ha-all",
            ".*",
            '{"ha-mode":"all","ha-sync-mode":"automatic"}',
            "--apply-to",
            "queues",
        ]
    )

    wait_until(
        policy_applied,
        timeout_sec=120,
        interval_sec=5,
        description="ha-all policy to apply on app-queue",
    )
    print("manual_policy_sync solver applied")


if __name__ == "__main__":
    main()
