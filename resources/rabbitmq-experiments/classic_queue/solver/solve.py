#!/usr/bin/env python3
import os
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from solver_utils import run, wait_deployment_ready, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")


def queue_is_classic_with_messages():
    out = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            "rabbitmq-0",
            "--",
            "rabbitmqctl",
            "-q",
            "list_queues",
            "-p",
            "/app",
            "name",
            "type",
            "messages",
        ]
    )
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0] == "app-queue":
            try:
                messages = int(parts[2])
            except ValueError:
                messages = 0
            return parts[1] == "classic" and messages >= 1
    return False


def main():
    run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            "rabbitmq-0",
            "--",
            "/bin/sh",
            "-lc",
            "rabbitmqctl clear_policy -p /app force-quorum || true",
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            "rabbitmq-0",
            "--",
            "/bin/sh",
            "-lc",
            "rabbitmqctl clear_policy -p / force-quorum || true",
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            "rabbitmq-0",
            "--",
            "rabbitmqctl",
            "set_permissions",
            "-p",
            "/app",
            "app-user",
            ".*",
            ".*",
            ".*",
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            "rabbitmq-0",
            "--",
            "/bin/sh",
            "-lc",
            "rabbitmqctl -q delete_queue -p /app app-queue || true",
        ]
    )

    run(["kubectl", "-n", NAMESPACE, "rollout", "restart", "deployment/app-producer"])
    wait_deployment_ready(NAMESPACE, "app-producer", timeout_sec=300)
    wait_until(
        queue_is_classic_with_messages,
        timeout_sec=180,
        interval_sec=5,
        description="app-queue classic with messages",
    )
    print("classic_queue solver applied")


if __name__ == "__main__":
    main()
