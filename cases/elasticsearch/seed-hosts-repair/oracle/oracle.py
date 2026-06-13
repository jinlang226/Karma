#!/usr/bin/env python3
import json
import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
EXPECTED_NODES = int(os.environ.get("BENCH_PARAM_EXPECTED_NODE_COUNT", "3"))
INDEX = os.environ.get("BENCH_PARAM_INDEX_NAME", "app-data")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


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

    health = curl_json(
        f"/_cluster/health?wait_for_status=yellow&wait_for_nodes={EXPECTED_NODES}&timeout=30s",
        errors,
    )
    if isinstance(health, dict):
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"Cluster health status expected yellow/green, got {status}")
        if health.get("number_of_nodes") != EXPECTED_NODES:
            errors.append(
                f"Expected {EXPECTED_NODES} nodes, got {health.get('number_of_nodes')}"
            )

    nodes = curl_json("/_cat/nodes?format=json", errors)
    if isinstance(nodes, list):
        if len(nodes) != EXPECTED_NODES:
            errors.append(f"Expected {EXPECTED_NODES} nodes in _cat/nodes, got {len(nodes)}")

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
