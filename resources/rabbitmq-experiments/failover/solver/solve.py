#!/usr/bin/env python3
import base64
import os
import re
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from solver_utils import kubectl_json, run, wait_statefulset_ready, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")


def cluster_reports_three_nodes():
    out = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            "rabbitmq-0",
            "--",
            "rabbitmqctl",
            "cluster_status",
        ]
    )
    nodes = set(re.findall(r"rabbit@[^\s,\]\}]+", out))
    return len(nodes) >= 3


def main():
    secret = kubectl_json("-n", NAMESPACE, "get", "secret", "rabbitmq-cookie-perpod")
    data = (secret.get("data") or {})
    cookie = ""
    raw = data.get("rabbitmq-0") or data.get("rabbitmq-1") or data.get("rabbitmq-2")
    if raw:
        cookie = base64.b64decode(raw).decode().strip()
    if not cookie:
        raise RuntimeError("unable to resolve baseline erlang cookie")

    manifest = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "create",
            "secret",
            "generic",
            "rabbitmq-cookie-perpod",
            f"--from-literal=rabbitmq-0={cookie}",
            f"--from-literal=rabbitmq-1={cookie}",
            f"--from-literal=rabbitmq-2={cookie}",
            "--dry-run=client",
            "-o",
            "yaml",
        ]
    )
    run(["kubectl", "-n", NAMESPACE, "apply", "-f", "-"], input_text=manifest)

    run(["kubectl", "-n", NAMESPACE, "rollout", "restart", "statefulset/rabbitmq"])
    wait_statefulset_ready(NAMESPACE, "rabbitmq", timeout_sec=900)
    wait_until(
        cluster_reports_three_nodes,
        timeout_sec=240,
        interval_sec=10,
        description="rabbitmq cluster to report 3 nodes",
    )
    print("failover solver applied")


if __name__ == "__main__":
    main()
