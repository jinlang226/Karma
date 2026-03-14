#!/usr/bin/env python3
import os
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from solver_utils import run, wait_deployment_ready, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")


def app_queue_has_messages():
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
            "messages",
        ]
    )
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == "app-queue":
            try:
                return int(parts[1]) >= 1
            except ValueError:
                return False
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
            "rabbitmqctl",
            "set_permissions",
            "-p",
            "/ops",
            "ops-user",
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
            "rabbitmqctl clear_permissions -p /app ops-user || true",
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
            "rabbitmqctl clear_permissions -p /ops app-user || true",
        ]
    )

    run(["kubectl", "-n", NAMESPACE, "rollout", "restart", "deployment/app-client"])
    run(["kubectl", "-n", NAMESPACE, "rollout", "restart", "deployment/ops-client"])
    wait_deployment_ready(NAMESPACE, "app-client", timeout_sec=300)
    wait_deployment_ready(NAMESPACE, "ops-client", timeout_sec=300)
    wait_until(
        app_queue_has_messages,
        timeout_sec=180,
        interval_sec=5,
        description="app-queue to contain messages",
    )
    print("manual_user_permission solver applied")


if __name__ == "__main__":
    main()
