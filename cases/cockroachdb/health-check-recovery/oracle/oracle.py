#!/usr/bin/env python3
import json
import os
import subprocess
import sys


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


_EXPECTED_NODES = None


def expected_nodes():
    """Resolve how many live nodes the cluster should have.

    This stage RECOVERS an inherited cluster; it does not change topology, so the
    expected count adapts to whatever was inherited: an explicit param override
    (BENCH_PARAM_EXPECTED_NODES / _REPLICA_COUNT) wins, else the live
    StatefulSet's DESIRED size (spec.replicas), else the old hardcoded 3. Using
    the desired size (not readyReplicas) keeps the check honest — if a node fails
    to recover, live_nodes < spec.replicas still fails. Cached.
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
    
    # Check all pods are healthy
    cmd = ["kubectl", "-n", "cockroachdb", "get", "pods", "-l", 
           "app.kubernetes.io/name=cockroachdb", "-o", "json"]
    result = run(cmd)
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            pods = data.get("items", [])
            for pod in pods:
                name = pod["metadata"]["name"]
                conditions = pod["status"].get("conditions", [])
                ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)
                if not ready:
                    errors.append(f"Pod {name} not ready")
        except (json.JSONDecodeError, KeyError):
            errors.append("Failed to parse pod status")

    # Check all nodes are live
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

    # Verify SQL readiness
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
        "SELECT 1;",
    ]
    result = run(cmd)
    if result.returncode != 0:
        errors.append(result.stderr.strip() or "SQL query failed")
    
    if errors:
        print("Health check recovery verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    
    print("All pods recovered and healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
