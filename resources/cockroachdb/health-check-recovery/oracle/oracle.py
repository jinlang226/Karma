#!/usr/bin/env python3
import json
import subprocess
import sys


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
        "--insecure",
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
