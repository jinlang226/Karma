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


_EXPECTED_NODES = None


def expected_nodes():
    """Resolve how many pods / live nodes the cluster should have.

    A partitioned rolling update does not change topology, so the expected count
    adapts to whatever was inherited: an explicit param override
    (BENCH_PARAM_EXPECTED_NODES / _REPLICA_COUNT) wins, else the live
    StatefulSet's DESIRED size (spec.replicas), else the old hardcoded 3. Using
    the desired size (not readyReplicas) keeps the check honest — a pod lost
    during the rollout still fails. Only the count target adapts. Cached.
    """
    global _EXPECTED_NODES
    if _EXPECTED_NODES is not None:
        return _EXPECTED_NODES
    override = (
        os.environ.get("BENCH_PARAM_EXPECTED_NODES")
        or os.environ.get("BENCH_PARAM_REPLICA_COUNT")
    )
    if override and override.strip().isdigit():
        _EXPECTED_NODES = int(override)
        return _EXPECTED_NODES
    result = run([
        "kubectl", "-n", "cockroachdb", "get", "statefulset", "crdb-cluster",
        "-o", "jsonpath={.spec.replicas}",
    ])
    if result.returncode == 0 and result.stdout.strip().isdigit():
        live = int(result.stdout.strip())
        if live > 0:
            _EXPECTED_NODES = live
            return _EXPECTED_NODES
    _EXPECTED_NODES = 3
    return _EXPECTED_NODES


_CONN_FLAG = None


def conn_flag():
    """Return the right cockroach SQL connection flag for the live cluster.

    Standalone this case runs against an INSECURE cluster (`--insecure`). But in
    a workflow this stage can inherit a SECURE cluster left running by a prior
    stage (e.g. certificate-rotation), whose precondition probe sees pods already
    Running and skips its own insecure redeploy. A hardcoded `--insecure` then
    fails with an SSL authentication error. Detect the mode once by checking for
    the mounted certs dir and connect accordingly so the same oracle works in
    both contexts. Mirrors cockroachdb/cluster-settings/oracle/oracle.py.
    """
    global _CONN_FLAG
    if _CONN_FLAG is not None:
        return _CONN_FLAG
    probe = run([
        "kubectl", "-n", "cockroachdb", "--request-timeout=15s", "exec",
        "crdb-cluster-0", "--", "ls", "/cockroach/cockroach-certs/ca.crt",
    ])
    if probe.returncode == 0:
        _CONN_FLAG = "--certs-dir=/cockroach/cockroach-certs"
    else:
        _CONN_FLAG = "--insecure"
    return _CONN_FLAG


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
        if len(pods) != expected_nodes():
            errors.append(f"Expected {expected_nodes()} pods, found {len(pods)}")
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
        conn_flag(),
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
        conn_flag(),
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
                if live_nodes != expected_nodes():
                    errors.append(f"Expected {expected_nodes()} live nodes, found {live_nodes}")

    if errors:
        print("Partitioned update verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Partitioned update completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
