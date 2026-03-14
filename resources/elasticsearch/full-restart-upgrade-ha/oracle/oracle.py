#!/usr/bin/env python3
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
TARGET_VERSION = os.environ.get("BENCH_PARAM_TO_VERSION", "8.11.1")
EXPECTED_NODES = int(os.environ.get("BENCH_PARAM_EXPECTED_NODES", "3"))
SEED_CONFIGMAP = os.environ.get("BENCH_PARAM_SEED_CONFIGMAP_NAME", "es-seed")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def curl(path, errors):
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        "curl-test",
        "--",
        "/bin/sh",
        "-c",
        f"curl -s -S --max-time 10 http://{SERVICE}:9200{path}",
    ]
    result = run(cmd)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        detail = stderr or stdout or f"command terminated with exit code {result.returncode}"
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

    seed_result = run(["kubectl", "-n", NAMESPACE, "get", "configmap", SEED_CONFIGMAP, "-o", "json"])
    if seed_result.returncode != 0:
        errors.append(f"Failed to read {SEED_CONFIGMAP} ConfigMap: {seed_result.stderr.strip()}")
        index = None
        expected = None
    else:
        try:
            seed = json.loads(seed_result.stdout)
        except json.JSONDecodeError:
            errors.append(f"Failed to parse {SEED_CONFIGMAP} ConfigMap JSON")
            seed = {}
        index = seed.get("data", {}).get("index")
        expected = seed.get("data", {}).get("expected_count")

    root = curl("/", errors)
    if isinstance(root, dict):
        version = root.get("version", {}).get("number")
        if version != TARGET_VERSION:
            errors.append(f"Expected version {TARGET_VERSION}, got {version}")
    else:
        errors.append("Failed to read Elasticsearch root version")

    health = curl(
        f"/_cluster/health?wait_for_status=yellow&wait_for_nodes={EXPECTED_NODES}&timeout=30s",
        errors,
    )
    if isinstance(health, dict):
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"Cluster health status expected yellow/green, got {status}")
        if health.get("number_of_nodes") != EXPECTED_NODES:
            errors.append(f"Expected {EXPECTED_NODES} nodes, got {health.get('number_of_nodes')}")
    else:
        errors.append("Failed to read cluster health")

    nodes = curl("/_cat/nodes?format=json", errors)
    if isinstance(nodes, list) and len(nodes) != EXPECTED_NODES:
        errors.append(f"Expected {EXPECTED_NODES} nodes in _cat/nodes, got {len(nodes)}")

    if index and expected:
        count = curl(f"/{index}/_count", errors)
        if isinstance(count, dict):
            actual = count.get("count")
            try:
                expected_val = int(expected)
            except ValueError:
                errors.append(f"Invalid expected_count value: {expected}")
            else:
                if actual != expected_val:
                    errors.append(f"Expected {expected_val} docs in {index}, got {actual}")

    sts_result = run(["kubectl", "-n", NAMESPACE, "get", "sts", CLUSTER_PREFIX, "-o", "json"])
    if sts_result.returncode == 0:
        try:
            sts = json.loads(sts_result.stdout)
        except json.JSONDecodeError:
            sts = {}
        containers = sts.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        if containers:
            image = containers[0].get("image")
            if image and TARGET_VERSION not in image:
                errors.append(f"StatefulSet image not upgraded: {image}")
    else:
        errors.append(f"Failed to read StatefulSet: {sts_result.stderr.strip()}")

    settings = curl("/_cluster/settings", errors)
    if isinstance(settings, dict):
        for scope in ("persistent", "transient"):
            allocation = settings.get(scope, {}).get("cluster", {}).get("routing", {}).get("allocation", {})
            if allocation.get("enable") == "none":
                errors.append("Shard allocation still disabled")
                break

    if errors:
        print("Full restart upgrade verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Full restart upgrade (HA) verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
