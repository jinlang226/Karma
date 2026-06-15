#!/usr/bin/env python3
# Verify the rolling partitioned update landed on the configured target version.
# The target version comes from the case param (BENCH_PARAM_TO_VERSION), so a
# workflow that overrides to_version is honored instead of a hardcoded value.
# Standalone (default param) this behaves identically to the old hardcoded check.
import json
import os
import subprocess
import sys


TO_VERSION = os.environ.get("BENCH_PARAM_TO_VERSION", "24.1.1")
TARGET_IMAGE = f"cockroachdb/cockroach:v{TO_VERSION}"
TARGET_VERSION = f"v{TO_VERSION}"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


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

    cmd = ["kubectl", "-n", "cockroachdb", "get", "statefulset", "crdb-cluster", "-o", "json"]
    result = run(cmd)
    if result.returncode != 0:
        errors.append(result.stderr.strip() or "Failed to read StatefulSet")
    else:
        try:
            sts = json.loads(result.stdout)
        except json.JSONDecodeError:
            errors.append("Failed to parse StatefulSet")
            sts = {}
        spec = sts.get("spec", {})
        status = sts.get("status", {})
        replicas = spec.get("replicas", 0)
        updated = status.get("updatedReplicas", 0)
        if updated != replicas:
            errors.append(f"Update not complete: {updated}/{replicas} updated")
        if status.get("currentRevision") != status.get("updateRevision"):
            errors.append("StatefulSet revisions do not match")
        partition = (
            spec.get("updateStrategy", {})
            .get("rollingUpdate", {})
            .get("partition", 0)
        )
        if partition not in (0, "0", None):
            errors.append(f"Partition not reset: {partition}")

    cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "get",
        "pods",
        "-l",
        "app.kubernetes.io/name=cockroachdb",
        "-o",
        "json",
    ]
    result = run(cmd)
    if result.returncode != 0:
        errors.append(result.stderr.strip() or "Failed to read pods")
    else:
        try:
            data = json.loads(result.stdout)
            pods = data.get("items", [])
        except json.JSONDecodeError:
            errors.append("Failed to parse pod list")
            pods = []
        if len(pods) != 3:
            errors.append(f"Expected 3 pods, found {len(pods)}")
        for pod in pods:
            name = pod.get("metadata", {}).get("name", "unknown")
            containers = pod.get("spec", {}).get("containers", [])
            if not containers:
                errors.append(f"No containers found in pod {name}")
                continue
            image = containers[0].get("image")
            if image != TARGET_IMAGE:
                errors.append(f"Pod {name} image is {image}")

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
        "-e",
        "SELECT version();",
    ]
    result = run(cmd)
    if result.returncode != 0:
        errors.append(result.stderr.strip() or "SQL version check failed")
    elif TARGET_VERSION not in result.stdout:
        errors.append("SQL version does not match target")

    cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "crdb-cluster-0",
        "--",
        "./cockroach",
        "node",
        "status",
        "--insecure",
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
                if live_nodes != 3:
                    errors.append(f"Expected 3 live nodes, found {live_nodes}")

    if errors:
        print("Partitioned update verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Partitioned update completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
