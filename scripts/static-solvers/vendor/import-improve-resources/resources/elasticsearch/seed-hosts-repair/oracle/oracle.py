#!/usr/bin/env python3
import json
import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")
INDEX = os.environ.get("BENCH_PARAM_INDEX_NAME", "app-data")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def expected_nodes(errors):
    result = run(["kubectl", "-n", NAMESPACE, "get", "statefulset", CLUSTER_PREFIX, "-o", "json"])
    if result.returncode != 0:
        errors.append(f"Failed to read StatefulSet {CLUSTER_PREFIX}")
        return None
    try:
        return int(json.loads(result.stdout).get("spec", {}).get("replicas", 0))
    except (TypeError, ValueError, json.JSONDecodeError):
        errors.append(f"StatefulSet {CLUSTER_PREFIX} has an invalid replica count")
        return None


def curl_json(path, errors):
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        "curl-test",
        "--",
        "curl",
        "-s",
        "-S",
        "--max-time",
        "10",
        f"http://{SERVICE}:9200{path}",
    ]
    result = run(cmd)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"command terminated with exit code {result.returncode}"
        errors.append(f"Failed to query {path}: {detail}")
        return None
    output = result.stdout.strip()
    if not output:
        errors.append(f"Empty response for {path}")
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        errors.append(f"Failed to parse JSON from {path}")
        return None


def main():
    errors = []
    expected = expected_nodes(errors)

    health = curl_json(
        "/_cluster/health?wait_for_status=yellow&timeout=30s",
        errors,
    )
    if isinstance(health, dict):
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"Cluster health status expected yellow/green, got {status}")
        if expected is not None and health.get("number_of_nodes") != expected:
            errors.append(
                f"Expected {expected} nodes, got {health.get('number_of_nodes')}"
            )

    nodes = curl_json("/_cat/nodes?format=json", errors)
    if isinstance(nodes, list):
        if expected is not None and len(nodes) != expected:
            errors.append(f"Expected {expected} nodes in _cat/nodes, got {len(nodes)}")

    count = curl_json(f"/{INDEX}/_count", errors)
    if isinstance(count, dict):
        if "count" not in count:
            errors.append("Unable to verify app-data count")

    if errors:
        print("Seed hosts repair verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Seed hosts repair verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
