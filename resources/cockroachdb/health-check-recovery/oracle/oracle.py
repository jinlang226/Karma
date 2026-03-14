#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_param_int,
    cluster_pod,
    cluster_prefix,
    kubectl_json,
    parse_tsv,
    run,
    to_bool,
)


def main():
    namespace = bench_namespace("cockroachdb")
    prefix = cluster_prefix("crdb-cluster")
    pod0 = cluster_pod(prefix, 0)
    replica_count = bench_param_int("replica_count", 3)

    errors = []

    pods_payload, pods_err = kubectl_json(
        namespace,
        ["get", "pods", "-l", "app.kubernetes.io/name=cockroachdb"],
    )
    if pods_err:
        errors.append(f"Failed to read pod status: {pods_err}")
    else:
        pods = pods_payload.get("items", [])
        if len(pods) != replica_count:
            errors.append(f"Expected {replica_count} pods, found {len(pods)}")
        for pod in pods:
            name = pod.get("metadata", {}).get("name", "unknown")
            conditions = pod.get("status", {}).get("conditions", [])
            ready = any(
                cond.get("type") == "Ready" and cond.get("status") == "True"
                for cond in conditions
            )
            if not ready:
                errors.append(f"Pod {name} not ready")

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
        "--format=tsv",
    ]
    node_status_result = run(node_status_cmd)
    if node_status_result.returncode != 0:
        errors.append(node_status_result.stderr.strip() or "Failed to read node status")
    else:
        header, rows = parse_tsv(node_status_result.stdout)
        if not header:
            errors.append("Empty node status output")
        else:
            cols = {name: idx for idx, name in enumerate(header)}
            live_idx = cols.get("is_live")
            if live_idx is None:
                errors.append("Missing is_live column in node status output")
            else:
                live_nodes = 0
                for row in rows:
                    if len(row) <= live_idx:
                        continue
                    if to_bool(row[live_idx]):
                        live_nodes += 1
                if live_nodes != replica_count:
                    errors.append(f"Expected {replica_count} live nodes, found {live_nodes}")

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
    sql_result = run(sql_cmd)
    if sql_result.returncode != 0:
        errors.append(sql_result.stderr.strip() or "SQL query failed")

    if errors:
        print("Health check recovery verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("All pods recovered and healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
