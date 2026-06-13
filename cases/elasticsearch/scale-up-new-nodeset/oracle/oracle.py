#!/usr/bin/env python3
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
EXPECTED_NODES = int(os.environ.get("BENCH_PARAM_EXPECTED_NODES", "5"))
ORIGINAL_STS = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")
INDEX_NAME = os.environ.get("BENCH_PARAM_INDEX_NAME", "app-data")
ORIGINAL_REPLICAS = int(os.environ.get("BENCH_PARAM_ORIGINAL_REPLICAS", "3"))


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


def get_original_nodes(errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "pods",
            "-l",
            f"app={ORIGINAL_STS}",
            "-o",
            "json",
        ]
    )
    if result.returncode != 0:
        errors.append(f"Failed to list Elasticsearch pods: {result.stderr.strip()}")
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        errors.append("Failed to parse pod list JSON")
        return []

    names = []
    for item in payload.get("items", []):
        for owner in item.get("metadata", {}).get("ownerReferences", []):
            if owner.get("kind") == "StatefulSet" and owner.get("name") == ORIGINAL_STS:
                names.append(item.get("metadata", {}).get("name"))
                break
    return names


def get_sts_replicas(errors):
    result = run(["kubectl", "-n", NAMESPACE, "get", "sts", ORIGINAL_STS, "-o", "json"])
    if result.returncode != 0:
        errors.append(f"Failed to read StatefulSet {ORIGINAL_STS}: {result.stderr.strip()}")
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        errors.append("Failed to parse StatefulSet JSON")
        return None
    return payload.get("spec", {}).get("replicas")


def attribute_differs(attributes_by_node, original_nodes, new_nodes):
    if not original_nodes or not new_nodes:
        return False
    original_attrs = [attributes_by_node.get(n, {}) for n in original_nodes]
    candidate_keys = set.intersection(*(set(a.keys()) for a in original_attrs if a)) if original_attrs else set()
    for key in sorted(candidate_keys):
        values = {a.get(key) for a in original_attrs}
        if len(values) != 1:
            continue
        original_value = next(iter(values))
        for node in new_nodes:
            node_attrs = attributes_by_node.get(node, {})
            if key in node_attrs and node_attrs.get(key) != original_value:
                return True
    return False


def main():
    errors = []

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

    nodes = curl("/_cat/nodes?format=json", errors)
    node_names = []
    if isinstance(nodes, list):
        node_names = [n.get("name") for n in nodes if n.get("name")]
        if len(node_names) != EXPECTED_NODES:
            errors.append(f"Expected {EXPECTED_NODES} nodes in _cat/nodes, got {len(node_names)}")

    original_nodes = get_original_nodes(errors)
    if original_nodes:
        new_nodes = [n for n in node_names if n not in original_nodes]
        if len(new_nodes) < 2:
            errors.append("Expected at least 2 new nodes outside original StatefulSet")
    else:
        new_nodes = []
        errors.append("Unable to determine original StatefulSet nodes")

    attrs = curl("/_nodes?filter_path=nodes.*.name,nodes.*.attributes", errors)
    if isinstance(attrs, dict):
        attributes_by_node = {}
        for node in attrs.get("nodes", {}).values():
            name = node.get("name")
            if name:
                attributes_by_node[name] = node.get("attributes", {})
        if original_nodes and new_nodes and not attribute_differs(attributes_by_node, original_nodes, new_nodes):
            errors.append("No allocation attribute differs between original nodes and new nodes")

    shards = curl(f"/_cat/shards/{INDEX_NAME}?format=json", errors)
    if isinstance(shards, list) and new_nodes:
        on_new = [s for s in shards if s.get("node") in new_nodes]
        if not on_new:
            errors.append(f"No {INDEX_NAME} shards found on new nodes")

    replicas = get_sts_replicas(errors)
    if replicas is not None and replicas != ORIGINAL_REPLICAS:
        errors.append(f"StatefulSet {ORIGINAL_STS} replicas expected {ORIGINAL_REPLICAS}, got {replicas}")

    if errors:
        print("Scale-up nodeset verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Scale-up new nodeset verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
