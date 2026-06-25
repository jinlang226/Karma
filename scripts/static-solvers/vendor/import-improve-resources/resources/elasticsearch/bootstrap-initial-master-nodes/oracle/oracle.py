#!/usr/bin/env python3
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")


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


def curl(path, errors):
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


def check_cluster(errors, expected):
    ep_result = run(["kubectl", "-n", NAMESPACE, "get", "endpoints", SERVICE, "-o", "json"])
    if ep_result.returncode != 0:
        errors.append(f"Failed to read {SERVICE} endpoints: {ep_result.stderr.strip()}")
        return
    try:
        ep = json.loads(ep_result.stdout)
    except json.JSONDecodeError:
        errors.append(f"Failed to parse {SERVICE} endpoints JSON")
        return

    addr_count = 0
    for subset in ep.get("subsets", []) or []:
        addr_count += len(subset.get("addresses", []) or [])
    if expected is not None and addr_count != expected:
        errors.append(f"Expected {expected} endpoints, got {addr_count}")

    health = curl(
        "/_cluster/health?wait_for_status=yellow&timeout=30s",
        errors,
    )
    if isinstance(health, dict):
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"Cluster health status expected yellow/green, got {status}")
        if expected is not None and health.get("number_of_nodes") != expected:
            errors.append(f"Expected {expected} nodes, got {health.get('number_of_nodes')}")

    root = curl("/", errors)
    if isinstance(root, dict):
        uuid = root.get("cluster_uuid")
        if not uuid or uuid == "_na_":
            errors.append("Cluster UUID not set")

    nodes = curl("/_cat/nodes?format=json", errors)
    if isinstance(nodes, list) and expected is not None and len(nodes) != expected:
        errors.append(f"Expected {expected} nodes in _cat/nodes, got {len(nodes)}")


def main():
    errors = []
    expected = expected_nodes(errors)

    cm_result = run(["kubectl", "-n", NAMESPACE, "get", "configmap", "es-config", "-o", "json"])
    if cm_result.returncode != 0:
        errors.append(f"Failed to read es-config ConfigMap: {cm_result.stderr.strip()}")
    else:
        try:
            cm = json.loads(cm_result.stdout)
        except json.JSONDecodeError:
            errors.append("Failed to parse es-config ConfigMap JSON")
            cm = {}
        config = cm.get("data", {}).get("elasticsearch.yml", "")
        if "cluster.initial_master_nodes" in config:
            errors.append("cluster.initial_master_nodes still present in es-config")

    pods_result = run(
        ["kubectl", "-n", NAMESPACE, "get", "pods", "-l", f"app={CLUSTER_PREFIX}", "-o", "json"]
    )
    if pods_result.returncode != 0:
        errors.append(f"Failed to read pods: {pods_result.stderr.strip()}")
    else:
        try:
            pods = json.loads(pods_result.stdout)
        except json.JSONDecodeError:
            errors.append("Failed to parse pods JSON")
            pods = {}
        items = pods.get("items", []) if isinstance(pods, dict) else []
        if expected is not None and len(items) != expected:
            errors.append(f"Expected {expected} pods, got {len(items)}")
        for pod in items:
            name = pod.get("metadata", {}).get("name", "unknown")
            conditions = pod.get("status", {}).get("conditions", [])
            ready = next((c for c in conditions if c.get("type") == "Ready"), {})
            if ready.get("status") != "True":
                errors.append(f"Pod {name} is not Ready")

    check_cluster(errors, expected)

    if errors:
        print("Bootstrap verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Bootstrap initial master nodes verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
