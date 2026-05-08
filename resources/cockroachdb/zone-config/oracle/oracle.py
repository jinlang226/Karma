#!/usr/bin/env python3
import subprocess
import sys


DB = "defaultdb"
TENANT_A = "tenant_a"
TENANT_B = "tenant_b"
EXPECTED = [
    "num_replicas: 3",
    "gc:",
    "ttlseconds: 14400",
    "range_min_bytes: 134217728",
    "range_max_bytes: 536870912",
]


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


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


def sql(query):
    cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "crdb-cluster-0",
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
    errors = []

    result = sql(
        f"SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema = '{TENANT_A}' AND table_type = 'BASE TABLE' "
        f"ORDER BY table_name;"
    )
    if result.returncode != 0:
        errors.append(result.stderr.strip() or "Failed to list tenant_a tables")
        tenant_a_tables = []
    else:
        tenant_a_tables = parse_table_list(result.stdout)

    result = sql(
        f"SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema = '{TENANT_B}' AND table_type = 'BASE TABLE' "
        f"ORDER BY table_name;"
    )
    if result.returncode != 0:
        errors.append(result.stderr.strip() or "Failed to list tenant_b tables")
        tenant_b_tables = []
    else:
        tenant_b_tables = parse_table_list(result.stdout)

    if not tenant_b_tables:
        errors.append("No tenant_b tables found")

    for table in tenant_b_tables:
        result = sql(
            "SELECT full_config_yaml FROM crdb_internal.zones "
            f"WHERE target LIKE 'TABLE %.{TENANT_B}.{table}';"
        )
        if result.returncode != 0:
            errors.append(f"Failed to read zone config for tenant_b.{table}")
            continue
        if not result.stdout.strip():
            errors.append(f"Missing zone config for tenant_b.{table}")
            continue
        for expected in EXPECTED:
            if expected not in result.stdout:
                errors.append(f"tenant_b.{table} missing {expected}")

    for table in tenant_a_tables:
        result = sql(
            "SELECT count(*) FROM crdb_internal.zones "
            f"WHERE target LIKE 'TABLE %.{TENANT_A}.{table}';"
        )
        if result.returncode != 0:
            errors.append(f"Failed to check zone config for tenant_a.{table}")
            continue
        count_line = result.stdout.strip().splitlines()
        count_value = count_line[-1].strip() if count_line else ""
        if count_value and count_value.isdigit() and int(count_value) > 0:
            errors.append(f"tenant_a.{table} has custom zone config")
        elif not count_value.isdigit():
            errors.append(f"Unexpected count output for tenant_a.{table}")

    if errors:
        print("Zone config verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Zone configuration updated successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
