#!/usr/bin/env python3
"""Repair RabbitMQ failover by rejoining the missing broker cleanly."""

import base64
import os
import sys
from pathlib import Path

COMMON = (
    Path(__file__).resolve().parents[2]
    / "vendor"
    / "import-improve-resources"
    / "resources"
    / "rabbitmq-experiments"
    / "common"
)
sys.path.insert(0, str(COMMON))

from solver_utils import kubectl_json, run, wait_statefulset_ready, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")
SEED_POD = f"{CLUSTER_PREFIX}-0"


def rabbitmqctl(pod_name, *args):
    """Run rabbitmqctl inside one RabbitMQ broker pod."""
    return run(["kubectl", "-n", NAMESPACE, "exec", pod_name, "--", "rabbitmqctl", *args])


def parse_running_nodes(status_text):
    """Extract the cluster_status running-node section as nodenames."""
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


def pod_nodename(ordinal):
    """Return the Erlang nodename for one StatefulSet ordinal."""
    pod = f"{CLUSTER_PREFIX}-{ordinal}"
    return f"rabbit@{pod}.{CLUSTER_PREFIX}-headless.{NAMESPACE}.svc.cluster.local"


def seed_nodename():
    """Return the seed broker's Erlang nodename."""
    return pod_nodename(0)


def missing_cluster_ordinals():
    """Return the broker ordinals missing from cluster_status."""
    out = rabbitmqctl(SEED_POD, "cluster_status")
    running = set(parse_running_nodes(out))
    missing = []
    for ordinal in range(1, 3):
        if pod_nodename(ordinal) not in running:
            missing.append(ordinal)
    return missing


def repair_target_pod():
    """Resolve the one broker pod that needs to rejoin the cluster."""
    missing = missing_cluster_ordinals()
    if not missing:
        raise RuntimeError("rabbitmq cluster already reports all nodes")
    if len(missing) > 1:
        raise RuntimeError(f"multiple rabbitmq cluster members missing: {missing}")
    return f"{CLUSTER_PREFIX}-{missing[0]}"


def cluster_reports_three_nodes():
    """Return whether cluster_status reports all three broker nodenames."""
    out = rabbitmqctl(SEED_POD, "cluster_status")
    expected = {pod_nodename(i) for i in range(3)}
    return expected.issubset(set(parse_running_nodes(out)))


def pod_ready(pod_name):
    """Return whether one pod exists and all its containers are Ready."""
    try:
        payload = kubectl_json("-n", NAMESPACE, "get", "pod", pod_name)
    except RuntimeError as exc:
        if "NotFound" in str(exc):
            return False
        raise
    phase = (payload.get("status") or {}).get("phase")
    statuses = (payload.get("status") or {}).get("containerStatuses") or []
    return phase == "Running" and bool(statuses) and all(
        status.get("ready") for status in statuses
    )


def ensure_target_rejoins(target_pod):
    """Run the standard RabbitMQ cluster rejoin sequence on the target broker."""
    join_target = seed_nodename()
    for args in (("stop_app",), ("reset",), ("join_cluster", join_target), ("start_app",)):
        rabbitmqctl(target_pod, *args)


def restart_target_pod(target_pod):
    """Delete the drifted broker pod and wait for the replacement to become Ready."""
    run(["kubectl", "-n", NAMESPACE, "delete", "pod", target_pod, "--ignore-not-found=true"])
    wait_until(
        lambda: pod_ready(target_pod),
        timeout_sec=600,
        interval_sec=5,
        description=f"pod/{target_pod} to be recreated and become Ready",
    )


def main():
    """Repair the missing broker, then wait for the 3-node cluster to converge."""
    secret = kubectl_json("-n", NAMESPACE, "get", "secret", f"{CLUSTER_PREFIX}-cookie-perpod")
    data = (secret.get("data") or {})
    cookie = ""
    raw = (
        data.get(f"{CLUSTER_PREFIX}-0")
        or data.get(f"{CLUSTER_PREFIX}-1")
        or data.get(f"{CLUSTER_PREFIX}-2")
    )
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
            f"{CLUSTER_PREFIX}-cookie-perpod",
            f"--from-literal={CLUSTER_PREFIX}-0={cookie}",
            f"--from-literal={CLUSTER_PREFIX}-1={cookie}",
            f"--from-literal={CLUSTER_PREFIX}-2={cookie}",
            "--dry-run=client",
            "-o",
            "yaml",
        ]
    )
    run(["kubectl", "-n", NAMESPACE, "apply", "-f", "-"], input_text=manifest)
    target_pod = repair_target_pod()
    restart_target_pod(target_pod)
    try:
        ensure_target_rejoins(target_pod)
    except Exception:
        try:
            rabbitmqctl(
                SEED_POD,
                "forget_cluster_node",
                pod_nodename(int(target_pod.rsplit("-", 1)[1])),
            )
        except Exception:
            pass
        ensure_target_rejoins(target_pod)
    wait_statefulset_ready(NAMESPACE, CLUSTER_PREFIX, timeout_sec=900)
    wait_until(
        cluster_reports_three_nodes,
        timeout_sec=600,
        interval_sec=10,
        description="rabbitmq cluster to report 3 nodes",
    )
    print("failover solver applied")


if __name__ == "__main__":
    main()
