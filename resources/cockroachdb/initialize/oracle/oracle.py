#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_param_int,
    cluster_pod,
    cluster_prefix,
    kubectl_json,
    run,
)


def exec_with_timeout(cmd, timeout_seconds):
    try:
        return run(cmd, timeout=timeout_seconds), None
    except Exception as exc:  # subprocess.TimeoutExpired compatibility across py versions
        return None, str(exc)


def parse_args():
    parser = argparse.ArgumentParser(description="Verify CockroachDB initialization.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="Timeout in seconds for kubectl exec checks.",
    )
    return parser.parse_args()


def main(timeout_seconds):
    namespace = bench_namespace("cockroachdb")
    prefix = cluster_prefix("crdb-cluster")
    pod0 = cluster_pod(prefix, 0)
    replica_count = bench_param_int("replica_count", 3)

    errors = []

    payload, _ = kubectl_json(namespace, ["get", "crdbcluster"])
    if payload and payload.get("items"):
        errors.append("CrdbCluster CRs detected; operator/CRDs are not allowed")

    node_status_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod0,
        "--",
        "./cockroach",
        "node",
        "status",
        "--insecure",
    ]
    result, err = exec_with_timeout(node_status_cmd, timeout_seconds)
    if err:
        errors.append(f"Cluster not initialized - 'cockroach node status' {err}")
    elif result.returncode != 0:
        errors.append("Cluster not initialized - 'cockroach node status' failed")
        errors.append(f"Error: {result.stderr.strip()}")
    else:
        lines = result.stdout.strip().split("\n")
        data_lines = [line for line in lines if line.strip() and not line.startswith("id") and not line.startswith("--")]
        if len(data_lines) < replica_count:
            errors.append(f"Expected at least {replica_count} nodes, but found {len(data_lines)}")

    sql_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod0,
        "--",
        "./cockroach",
        "sql",
        "--insecure",
        "-e",
        "SELECT 1;",
    ]
    result, err = exec_with_timeout(sql_cmd, timeout_seconds)
    if err:
        errors.append(f"SQL connectivity test {err}")
    elif result.returncode != 0:
        errors.append("SQL connectivity test failed")
        errors.append(f"Error: {result.stderr.strip()}")

    pods_data, pods_err = kubectl_json(
        namespace,
        ["get", "pods", "-l", "app.kubernetes.io/name=cockroachdb"],
    )
    if pods_err:
        errors.append(f"Failed to read pod status: {pods_err}")
    else:
        pods = pods_data.get("items", [])
        if len(pods) != replica_count:
            errors.append(f"Expected {replica_count} pods, found {len(pods)}")
        for pod in pods:
            name = pod.get("metadata", {}).get("name", "unknown")
            phase = pod.get("status", {}).get("phase", "Unknown")
            conditions = pod.get("status", {}).get("conditions", [])
            ready = any(
                cond.get("type") == "Ready" and cond.get("status") == "True"
                for cond in conditions
            )
            if phase != "Running":
                errors.append(f"Pod {name} is not Running (phase: {phase})")
            if not ready:
                errors.append(f"Pod {name} is not Ready")

    if errors:
        print("Initialization verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Cluster initialized successfully")
    return 0


if __name__ == "__main__":
    args = parse_args()
    sys.exit(main(args.timeout))
