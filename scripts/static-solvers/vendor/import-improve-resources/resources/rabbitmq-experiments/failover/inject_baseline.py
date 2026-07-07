#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

COMMON = Path(__file__).resolve().parents[1] / "common"
sys.path.insert(0, str(COMMON))

from solver_utils import run, wait_until  # noqa: E402


def parse_running_nodes(status_text):
    nodes = []
    in_section = False
    for raw_line in status_text.splitlines():
        line = raw_line.strip()
        if not line:
            if in_section and nodes:
                break
            continue
        if line == "Running Nodes":
            in_section = True
            continue
        if not in_section:
            continue
        if line.startswith("rabbit@"):
            nodes.append(line)
            continue
        if nodes:
            break
    return nodes


def cluster_missing_target(namespace, cluster_prefix, target_node):
    try:
        out = run(
            [
                "kubectl",
                "-n",
                namespace,
                "exec",
                f"{cluster_prefix}-0",
                "--",
                "rabbitmqctl",
                "cluster_status",
            ]
        )
    except Exception:
        return False
    return target_node not in parse_running_nodes(out)


def pod_exists(namespace, pod_name):
    try:
        run(["kubectl", "-n", namespace, "get", "pod", pod_name])
        return True
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default=os.environ.get("BENCH_NAMESPACE", "rabbitmq"))
    args = parser.parse_args()

    namespace = args.namespace
    cluster_prefix = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")
    target_pod = f"{cluster_prefix}-2"
    target_node = (
        f"rabbit@{target_pod}.{cluster_prefix}-headless.{namespace}.svc.cluster.local"
    )

    manifest = run(
        [
            "kubectl",
            "-n",
            namespace,
            "create",
            "secret",
            "generic",
            f"{cluster_prefix}-cookie-perpod",
            f"--from-literal={cluster_prefix}-0=supersecretcookie",
            f"--from-literal={cluster_prefix}-1=supersecretcookie",
            f"--from-literal={cluster_prefix}-2=driftedcookie",
            "--dry-run=client",
            "-o",
            "yaml",
        ]
    )
    run(["kubectl", "-n", namespace, "apply", "-f", "-"], input_text=manifest)
    run(["kubectl", "-n", namespace, "delete", "pod", target_pod, "--ignore-not-found=true"])
    wait_until(
        lambda: pod_exists(namespace, target_pod),
        timeout_sec=120,
        interval_sec=2,
        description=f"{target_pod} to be recreated",
    )
    wait_until(
        lambda: cluster_missing_target(namespace, cluster_prefix, target_node),
        timeout_sec=180,
        interval_sec=5,
        description=f"{target_node} to drop out of running cluster membership",
    )
    time.sleep(2)


if __name__ == "__main__":
    main()
