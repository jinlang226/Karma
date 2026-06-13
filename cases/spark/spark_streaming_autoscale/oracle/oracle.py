#!/usr/bin/env python3
"""Oracle for spark/spark_streaming_autoscale.

Verifies the operator actively scaled the cluster through the traffic phases:
  - the Spark cluster is up (master ready, >= 1 worker),
  - at least two scaling events were recorded by the metrics-server (the agent
    must have run >= 2 `kubectl scale` operations on spark-worker), and
  - the traffic generator is running or has completed.
"""
from __future__ import annotations

import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import bench_namespace, deployment_ready_replicas, run  # noqa: E402

NAMESPACE = bench_namespace("spark-streaming")
MIN_SCALING_EVENTS = 2


def fail(message: str) -> int:
    print(f"spark_streaming_autoscale oracle failed: {message}")
    return 1


def pod_logs(selector: str) -> str:
    proc = run(
        ["kubectl", "-n", NAMESPACE, "logs", "-l", selector, "--tail=-1", "--prefix=true"],
        check=False,
    )
    return proc.stdout or ""


def check_cluster() -> int:
    master_ready = deployment_ready_replicas(NAMESPACE, "spark-master")
    if master_ready < 1:
        return fail(f"deployment/spark-master readyReplicas={master_ready}, expected >= 1")
    worker_ready = deployment_ready_replicas(NAMESPACE, "spark-worker")
    if worker_ready < 1:
        return fail(f"deployment/spark-worker readyReplicas={worker_ready}, expected >= 1")
    return 0


def check_scaling_events() -> int:
    logs = pod_logs("app=metrics-server")
    count = logs.count("SCALING EVENT")
    if count < MIN_SCALING_EVENTS:
        return fail(
            f"only {count} scaling event(s) recorded by metrics-server, expected >= {MIN_SCALING_EVENTS}"
        )
    return 0


def check_traffic_generator() -> int:
    proc = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "deployment",
            "traffic-generator",
            "-o",
            "jsonpath={.status.availableReplicas}",
        ],
        check=False,
    )
    if proc.returncode != 0:
        return fail("deployment/traffic-generator not found")
    raw = (proc.stdout or "").strip()
    try:
        available = int(raw or "0")
    except ValueError:
        available = 0
    logs = pod_logs("app=traffic-generator")
    if available < 1 and "TRAFFIC GENERATION COMPLETE" not in logs:
        return fail("traffic-generator is neither running nor completed")
    return 0


def main() -> int:
    for check in (check_cluster, check_scaling_events, check_traffic_generator):
        rc = check()
        if rc != 0:
            return rc
    print("spark_streaming_autoscale verified: cluster up, >= 2 scaling events, traffic active")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
