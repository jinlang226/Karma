#!/usr/bin/env python3
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
EXPECTED_NODES = int(os.environ.get("BENCH_PARAM_EXPECTED_NODES", "3"))
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


def check_cluster(errors):
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
    if addr_count != EXPECTED_NODES:
        errors.append(f"Expected {EXPECTED_NODES} endpoints, got {addr_count}")

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

    root = curl("/", errors)
    if isinstance(root, dict):
        uuid = root.get("cluster_uuid")
        if not uuid or uuid == "_na_":
            errors.append("Cluster UUID not set")

    nodes = curl("/_cat/nodes?format=json", errors)
    if isinstance(nodes, list) and len(nodes) != EXPECTED_NODES:
        errors.append(f"Expected {EXPECTED_NODES} nodes in _cat/nodes, got {len(nodes)}")


def main():
    errors = []

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
        if len(items) != EXPECTED_NODES:
            errors.append(f"Expected {EXPECTED_NODES} pods, got {len(items)}")
        for pod in items:
            name = pod.get("metadata", {}).get("name", "unknown")
            conditions = pod.get("status", {}).get("conditions", [])
            ready = next((c for c in conditions if c.get("type") == "Ready"), {})
            if ready.get("status") != "True":
                errors.append(f"Pod {name} is not Ready")

    check_cluster(errors)

    if errors:
        print("Bootstrap verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Bootstrap initial master nodes verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
