#!/usr/bin/env python3
import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[1] / "common"
sys.path.insert(0, str(COMMON))

from setup_check_utils import expect_pods_ready, list_pods, pod_is_ready, pod_phase, run_json  # noqa: E402


def run(cmd):
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default=os.environ.get("BENCH_NAMESPACE", "rabbitmq"))
    parser.add_argument("--min-ready", type=int, default=1)
    args = parser.parse_args()
    ns = args.namespace
    cluster_prefix = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")
    errors = []

    rabbit_pods = list_pods(ns, label=f"app={cluster_prefix}")
    if len(rabbit_pods) != 3:
        errors.append(f"{cluster_prefix}: expected 3 pods, found {len(rabbit_pods)}")
    ready_count = 0
    target_pod_name = f"{cluster_prefix}-2"
    for pod in rabbit_pods:
        name = (pod.get("metadata") or {}).get("name", "<unknown>")
        ready = pod_is_ready(pod)
        phase = pod_phase(pod)
        if ready:
            ready_count += 1
        if name != target_pod_name and not ready:
            errors.append(f"{name}: phase={phase}, ready={ready}")
    if ready_count < args.min_ready:
        errors.append(f"{cluster_prefix}: ready={ready_count}, expected>={args.min_ready}")
    expect_pods_ready(ns, "app=curl-test", 1, errors, "curl-test")

    try:
        sec = run_json(
            ["kubectl", "-n", ns, "get", "secret", f"{cluster_prefix}-cookie-perpod", "-o", "json"]
        )
        data = sec.get("data") or {}
        keys = tuple(f"{cluster_prefix}-{i}" for i in range(3))
        missing = [k for k in keys if k not in data]
        if missing:
            errors.append(f"cookie secret missing keys: {','.join(missing)}")
        else:
            values = [base64.b64decode(data[k]).decode().strip() for k in keys]
            if len(set(values)) == 1:
                errors.append("cookie drift precondition missing (all node cookies equal)")
    except Exception as exc:
        errors.append(f"failed to validate {cluster_prefix}-cookie-perpod: {exc}")

    try:
        cluster_status = run(
            [
                "kubectl",
                "-n",
                ns,
                "exec",
                f"{cluster_prefix}-0",
                "--",
                "rabbitmqctl",
                "cluster_status",
            ]
        )
        running_nodes = parse_running_nodes(cluster_status)
        expected_running = {
            f"rabbit@{cluster_prefix}-{i}.{cluster_prefix}-headless.{ns}.svc.cluster.local"
            for i in range(3)
        }
        missing = sorted(expected_running - set(running_nodes))
        if not missing:
            errors.append("failover precondition missing (cluster already reports all 3 running nodes)")
        expected_missing = (
            f"rabbit@{cluster_prefix}-2.{cluster_prefix}-headless.{ns}.svc.cluster.local"
        )
        if expected_missing not in missing:
            errors.append(
                "failover precondition missing expected node drift "
                f"(missing={','.join(missing) if missing else 'none'})"
            )
    except Exception as exc:
        errors.append(f"failed to inspect cluster running nodes: {exc}")

    if errors:
        print("setup-precondition-check: failed")
        for err in errors:
            print(f" - {err}")
        return 1
    print("setup-precondition-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
