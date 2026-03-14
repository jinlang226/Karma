#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_param,
    cluster_pod,
    cluster_prefix,
    run,
)


def parse_bytes(value):
    text = str(value).strip().lower().replace(" ", "")
    text = text.replace("/s", "")
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
        if text.endswith(suffix):
            raw = text[: -len(suffix)].strip()
            return int(float(raw) * units[suffix])
    return int(float(text))


def get_setting(namespace, pod_name, setting_name):
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
        "--format=tsv",
        "-e",
        f"SHOW CLUSTER SETTING {setting_name};",
    ]
    result = run(cmd)
    if result.returncode != 0:
        return None, result.stderr.strip() or "setting query failed"
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None, "empty setting output"
    return lines[-1], None


def main():
    namespace = bench_namespace("cockroachdb")
    prefix = cluster_prefix("crdb-cluster")
    pod0 = cluster_pod(prefix, 0)
    setting_name = bench_param("setting_name", "kv.snapshot_rebalance.max_rate")
    baseline_value = bench_param("setting_value", "1MiB")

    errors = []

    setting, err = get_setting(namespace, pod0, setting_name)
    if err:
        errors.append(f"Failed to check cluster setting {setting_name}: {err}")
    else:
        try:
            current_bytes = parse_bytes(setting)
            threshold = 64 * 1024 * 1024
            try:
                baseline_bytes = parse_bytes(baseline_value)
                threshold = max(threshold, baseline_bytes + 1)
            except ValueError:
                pass
            if current_bytes < threshold:
                errors.append(
                    f"Setting {setting_name} too low: {setting} (expected >= {threshold} bytes)"
                )
        except ValueError:
            errors.append(f"Unable to parse setting value: {setting}")

    if errors:
        print("Cluster settings verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Cluster settings updated successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
