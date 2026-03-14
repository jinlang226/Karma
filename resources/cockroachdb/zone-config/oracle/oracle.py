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
    run,
)


DB = "defaultdb"


def parse_table_list(output):
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    tables = []
    for line in lines:
        lower = line.lower()
        if lower in ("table_name", "table_name\t"):
            continue
        if line.startswith("(") or set(line) <= set("-+"):
            continue
        tables.append(line.split("\t")[0].strip())
    return tables


def sql(namespace, pod_name, query):
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
        "--database",
        DB,
        "--format=tsv",
        "-e",
        query,
    ]
    return run(cmd)


def main():
    namespace = bench_namespace("cockroachdb")
    prefix = cluster_prefix("crdb-cluster")
    pod0 = cluster_pod(prefix, 0)

    target_schema = bench_param("target_schema", "tenant_b")
    protected_schema = bench_param("protected_schema", "tenant_a")
    num_replicas = bench_param_int("num_replicas", 3)
    gc_ttl_seconds = bench_param_int("gc_ttl_seconds", 14400)
    range_min_bytes = bench_param_int("range_min_bytes", 134217728)
    range_max_bytes = bench_param_int("range_max_bytes", 536870912)

    expected_tokens = [
        f"num_replicas: {num_replicas}",
        "gc:",
        f"ttlseconds: {gc_ttl_seconds}",
        f"range_min_bytes: {range_min_bytes}",
        f"range_max_bytes: {range_max_bytes}",
    ]

    errors = []

    result = sql(
        namespace,
        pod0,
        (
            "SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema = '{protected_schema}' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name;"
        ),
    )
    if result.returncode != 0:
        errors.append(result.stderr.strip() or f"Failed to list {protected_schema} tables")
        protected_tables = []
    else:
        protected_tables = parse_table_list(result.stdout)

    result = sql(
        namespace,
        pod0,
        (
            "SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema = '{target_schema}' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name;"
        ),
    )
    if result.returncode != 0:
        errors.append(result.stderr.strip() or f"Failed to list {target_schema} tables")
        target_tables = []
    else:
        target_tables = parse_table_list(result.stdout)

    if not target_tables:
        errors.append(f"No {target_schema} tables found")

    for table in target_tables:
        result = sql(
            namespace,
            pod0,
            (
                "SELECT full_config_yaml FROM crdb_internal.zones "
                f"WHERE target LIKE 'TABLE %.{target_schema}.{table}';"
            ),
        )
        if result.returncode != 0:
            errors.append(f"Failed to read zone config for {target_schema}.{table}")
            continue
        if not result.stdout.strip():
            errors.append(f"Missing zone config for {target_schema}.{table}")
            continue
        output = result.stdout.lower()
        for expected in expected_tokens:
            if expected.lower() not in output:
                errors.append(f"{target_schema}.{table} missing {expected}")

    for table in protected_tables:
        result = sql(
            namespace,
            pod0,
            (
                "SELECT count(*) FROM crdb_internal.zones "
                f"WHERE target LIKE 'TABLE %.{protected_schema}.{table}';"
            ),
        )
        if result.returncode != 0:
            errors.append(f"Failed to check zone config for {protected_schema}.{table}")
            continue
        count_line = result.stdout.strip().splitlines()
        count_value = count_line[-1].strip() if count_line else ""
        if count_value and count_value.isdigit() and int(count_value) > 0:
            errors.append(f"{protected_schema}.{table} has custom zone config")
        elif not count_value.isdigit():
            errors.append(f"Unexpected count output for {protected_schema}.{table}")

    if errors:
        print("Zone config verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Zone configuration updated successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
