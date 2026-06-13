#!/usr/bin/env python3
import subprocess
import sys


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def parse_bytes(value):
    value = value.strip().lower().replace(" ", "")
    value = value.replace("/s", "")
    units = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000 ** 2,
        "mib": 1024 ** 2,
        "gb": 1000 ** 3,
        "gib": 1024 ** 3,
        "tb": 1000 ** 4,
        "tib": 1024 ** 4,
    }
    for suffix in sorted(units.keys(), key=len, reverse=True):
        factor = units[suffix]
        if value.endswith(suffix):
            num = value[: -len(suffix)].strip()
            return int(float(num) * factor)
    return int(float(value))


def get_setting():
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
        "--format=tsv",
        "-e",
        "SHOW CLUSTER SETTING kv.snapshot_rebalance.max_rate;",
    ]
    result = run(cmd)
    if result.returncode != 0:
        return None, result.stderr.strip()
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None, "Empty setting output"
    return lines[-1], None


def main():
    errors = []

    setting, err = get_setting()
    if err:
        errors.append("Failed to check cluster setting")
        errors.append(f"Error: {err}")
    else:
        try:
            value_bytes = parse_bytes(setting)
            if value_bytes < 64 * 1024 * 1024:
                errors.append(f"Setting too low: {setting}")
        except ValueError:
            errors.append(f"Unable to parse setting value: {setting}")

    delete_cmd = ["kubectl", "-n", "cockroachdb", "delete", "pod", "crdb-cluster-0"]
    delete_result = run(delete_cmd)
    if delete_result.returncode != 0:
        errors.append("Failed to delete pod for persistence check")
        errors.append(f"Error: {delete_result.stderr.strip()}")

    wait_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "wait",
        "--for=condition=ready",
        "pod/crdb-cluster-0",
        "--timeout=120s",
    ]
    wait_result = run(wait_cmd)
    if wait_result.returncode != 0:
        errors.append("Pod did not become ready after restart")
        errors.append(f"Error: {wait_result.stderr.strip()}")

    setting_after, err_after = get_setting()
    if err_after:
        errors.append("Failed to check cluster setting after restart")
        errors.append(f"Error: {err_after}")
    else:
        try:
            value_bytes = parse_bytes(setting_after)
            if value_bytes < 64 * 1024 * 1024:
                errors.append(f"Setting too low after restart: {setting_after}")
        except ValueError:
            errors.append(f"Unable to parse setting value after restart: {setting_after}")

    if errors:
        print("Cluster settings verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    
    print("Cluster settings updated successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
