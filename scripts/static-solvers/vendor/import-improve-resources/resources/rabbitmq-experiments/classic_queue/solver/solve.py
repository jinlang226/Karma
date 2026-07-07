#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from solver_utils import run, wait_deployment_ready, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")
SEED_POD = f"{CLUSTER_PREFIX}-0"


def queue_is_classic_with_messages():
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


def queue_is_classic_with_messages_via_api():
    try:
        out = run(
            [
                "kubectl",
                "-n",
                NAMESPACE,
                "exec",
                "curl-test",
                "--",
                "/bin/sh",
                "-lc",
                (
                    "curl -s -u admin:adminpass "
                    f"http://{CLUSTER_PREFIX}:15672/api/queues/%2Fapp/app-queue"
                ),
            ]
        )
        payload = json.loads(out)
    except Exception:
        return False
    queue_type = payload.get("type")
    try:
        messages = int(payload.get("messages", 0))
    except (TypeError, ValueError):
        messages = 0
    return queue_type == "classic" and messages >= 1


def main():
    run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            SEED_POD,
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
            SEED_POD,
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
            SEED_POD,
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
            SEED_POD,
            "--",
            "/bin/sh",
            "-lc",
            "rabbitmqctl -q delete_queue -p /app app-queue || true",
        ]
    )

    run(["kubectl", "-n", NAMESPACE, "rollout", "restart", "deployment/app-producer"])
    wait_deployment_ready(NAMESPACE, "app-producer", timeout_sec=300)
    wait_until(
        queue_is_classic_with_messages_via_api,
        timeout_sec=300,
        interval_sec=5,
        description="app-queue classic with messages via management API",
    )
    print("classic_queue solver applied")


if __name__ == "__main__":
    main()
