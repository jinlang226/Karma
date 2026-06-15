#!/usr/bin/env python3
# Verify the cluster was safely decommissioned to 3 nodes with the seeded data
# preserved. The seeded table name comes from the case param
# (BENCH_PARAM_SEED_TABLE_NAME), so a workflow that overrides it is honored
# instead of a hardcoded value. Standalone (default param) this behaves
# identically.
import json
import os
import subprocess
import sys


NAMESPACE = "cockroachdb"
POD = "crdb-cluster-0"
SQL_HOST = "crdb-cluster-0.crdb-cluster.cockroachdb.svc.cluster.local"
TARGET_PODS = ["crdb-cluster-3", "crdb-cluster-4"]
SEED_TABLE = os.environ.get("BENCH_PARAM_SEED_TABLE_NAME", "bench.decom_data")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def exec_sql(sql, fmt=None):
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        POD,
        "--",
        "./cockroach",
        "sql",
        "--insecure",
        f"--host={SQL_HOST}",
    ]
    if fmt:
        cmd.append(f"--format={fmt}")
    cmd += ["-e", sql]
    return run(cmd)


def parse_tsv(output):
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return [], []
    header = lines[0].split("\t")
    rows = [line.split("\t") for line in lines[1:]]
    return header, rows


def to_bool(value):
    return str(value).strip().lower() in ("true", "t", "1", "yes")


def main():
    errors = []

    cmd = ["kubectl", "-n", NAMESPACE, "get", "statefulset", "crdb-cluster", "-o", "json"]
    result = run(cmd)
    if result.returncode == 0:
        try:
            sts = json.loads(result.stdout)
            replicas = sts.get("spec", {}).get("replicas", 0)
            ready = sts.get("status", {}).get("readyReplicas", 0)
            if replicas != 3:
                errors.append(f"StatefulSet should have 3 replicas, got {replicas}")
            if ready < 3:
                errors.append(f"StatefulSet readyReplicas should be 3, got {ready}")
        except json.JSONDecodeError:
            errors.append("Failed to parse StatefulSet JSON")
    else:
        errors.append(result.stderr.strip() or "Failed to get StatefulSet")

    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        POD,
        "--",
        "./cockroach",
        "node",
        "status",
        "--insecure",
        "--decommission",
        "--format=tsv",
    ]
    result = run(cmd)
    if result.returncode != 0:
        errors.append(result.stderr.strip() or "Failed to read node status")
    else:
        header, rows = parse_tsv(result.stdout)
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
                    "Missing expected columns in node status output "
                    "(need address, is_live, and one of membership, is_decommissioned, "
                    "is_decommissioning)"
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
                    for pod in TARGET_PODS:
                        if pod in address:
                            target_states[pod] = is_decommissioned
                if live_active != 3:
                    errors.append(f"Expected 3 live nodes, found {live_active}")
                for pod in TARGET_PODS:
                    if pod not in target_states:
                        errors.append(f"Missing node status for {pod}")
                    elif not target_states[pod]:
                        errors.append(f"{pod} is not decommissioned")

    result = exec_sql(f"SELECT count(*) FROM {SEED_TABLE};", fmt="tsv")
    if result.returncode != 0:
        errors.append(result.stderr.strip() or f"Failed to query {SEED_TABLE}")
    else:
        header, rows = parse_tsv(result.stdout)
        if not rows:
            errors.append("No rows returned for data check")
        else:
            try:
                count = int(rows[0][0])
                if count < 3:
                    errors.append(f"Expected at least 3 rows, got {count}")
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
