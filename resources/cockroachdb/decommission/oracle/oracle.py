#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_param,
    bench_param_int,
    cluster_pod,
    cluster_prefix,
    cluster_sql_host,
    parse_tsv,
    run,
    to_bool,
)


def exec_sql(namespace, pod_name, sql_host, sql, fmt=None):
    cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod_name,
        "--",
        "./cockroach",
        "sql",
        "--insecure",
        f"--host={sql_host}",
    ]
    if fmt:
        cmd.append(f"--format={fmt}")
    cmd += ["-e", sql]
    return run(cmd)


def main():
    namespace = bench_namespace("cockroachdb")
    prefix = cluster_prefix("crdb-cluster")
    pod0 = cluster_pod(prefix, 0)
    sql_host = cluster_sql_host(prefix, namespace, 0)
    from_replica_count = bench_param_int("from_replica_count", 4)
    to_replica_count = bench_param_int("to_replica_count", 3)
    seed_table_name = bench_param("seed_table_name", "bench.decom_data")
    seed_row_count_min = bench_param_int("seed_row_count_min", 3)

    target_pods = [f"{prefix}-{idx}" for idx in range(to_replica_count, from_replica_count)]

    errors = []

    sts_result = run(["kubectl", "-n", namespace, "get", "statefulset", prefix, "-o", "json"])
    if sts_result.returncode != 0:
        errors.append(sts_result.stderr.strip() or "Failed to get StatefulSet")
    else:
        try:
            import json

            sts = json.loads(sts_result.stdout)
            replicas = sts.get("spec", {}).get("replicas", 0)
            ready = sts.get("status", {}).get("readyReplicas", 0)
            if replicas != to_replica_count:
                errors.append(
                    f"StatefulSet should have {to_replica_count} replicas, got {replicas}"
                )
            if ready < to_replica_count:
                errors.append(
                    f"StatefulSet readyReplicas should be >= {to_replica_count}, got {ready}"
                )
        except Exception as exc:
            errors.append(f"Failed to parse StatefulSet JSON: {exc}")

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
        "--decommission",
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
            addr_idx = cols.get("address")
            live_idx = cols.get("is_live")
            membership_idx = cols.get("membership")
            decom_idx = cols.get("is_decommissioned")
            decom_ing_idx = cols.get("is_decommissioning")
            if addr_idx is None or live_idx is None or (
                membership_idx is None and decom_idx is None and decom_ing_idx is None
            ):
                errors.append(
                    "Missing expected node status columns (address, is_live, membership/decommission)"
                )
            else:
                live_active = 0
                target_states = {}
                for row in rows:
                    max_idx = max(
                        addr_idx,
                        live_idx,
                        membership_idx if membership_idx is not None else -1,
                        decom_idx if decom_idx is not None else -1,
                        decom_ing_idx if decom_ing_idx is not None else -1,
                    )
                    if len(row) <= max_idx:
                        continue
                    address = row[addr_idx]
                    is_live = to_bool(row[live_idx])
                    if membership_idx is not None:
                        membership = row[membership_idx].strip().lower()
                        is_decommissioned = membership == "decommissioned"
                    elif decom_idx is not None:
                        is_decommissioned = to_bool(row[decom_idx])
                    else:
                        is_decommissioned = to_bool(row[decom_ing_idx])
                    if is_live and not is_decommissioned:
                        live_active += 1
                    for pod_name in target_pods:
                        if pod_name in address:
                            target_states[pod_name] = is_decommissioned

                if live_active != to_replica_count:
                    errors.append(f"Expected {to_replica_count} live nodes, found {live_active}")

                for pod_name in target_pods:
                    if pod_name not in target_states:
                        errors.append(f"Missing node status for {pod_name}")
                    elif not target_states[pod_name]:
                        errors.append(f"{pod_name} is not decommissioned")

    data_result = exec_sql(
        namespace,
        pod0,
        sql_host,
        f"SELECT count(*) FROM {seed_table_name};",
        fmt="tsv",
    )
    if data_result.returncode != 0:
        errors.append(data_result.stderr.strip() or f"Failed to query {seed_table_name}")
    else:
        _, rows = parse_tsv(data_result.stdout)
        if not rows:
            errors.append("No rows returned for data check")
        else:
            try:
                count = int(rows[0][0])
                if count < seed_row_count_min:
                    errors.append(f"Expected at least {seed_row_count_min} rows, got {count}")
            except ValueError:
                errors.append("Failed to parse row count")

    if errors:
        print("Decommission verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Decommission verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
