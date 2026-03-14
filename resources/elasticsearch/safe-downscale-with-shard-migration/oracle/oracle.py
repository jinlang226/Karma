#!/usr/bin/env python3
import json
import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
STS_NAME = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")
PVC_PREFIX = os.environ.get("BENCH_PARAM_PVC_NAME_PREFIX", "data-es-cluster-")
MARKER_PATH = "/usr/share/elasticsearch/data/pvc-gc-marker"
EXPECTED_REPLICAS = int(os.environ.get("BENCH_PARAM_TARGET_REPLICAS", "1"))


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def curl(path, errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            "curl-test",
            "--",
            "/bin/sh",
            "-c",
            f"curl -s -S --connect-timeout 2 --max-time 3 http://{SERVICE}:9200{path}",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
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


def get_json(cmd, errors, label):
    result = run(cmd)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to read {label}: {detail}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        errors.append(f"Failed to parse {label} JSON")
        return None


def pod_ready(pod):
    for condition in pod.get("status", {}).get("conditions", []):
        if condition.get("type") == "Ready":
            return condition.get("status") == "True"
    return False


def pvc_ordinal(name):
    if not name.startswith(PVC_PREFIX):
        return None
    suffix = name[len(PVC_PREFIX) :]
    if not suffix.isdigit():
        return None
    return int(suffix)


def main():
    errors = []

    sts_data = get_json(
        ["kubectl", "-n", NAMESPACE, "get", "sts", STS_NAME, "-o", "json"],
        errors,
        f"StatefulSet {STS_NAME}",
    )
    replicas = None
    if sts_data:
        replicas = sts_data.get("spec", {}).get("replicas")
        if replicas != EXPECTED_REPLICAS:
            errors.append(f"StatefulSet replicas expected {EXPECTED_REPLICAS}, got {replicas}")
    else:
        errors.append("Unable to read StatefulSet replicas")
        replicas = 0

    pods_data = get_json(
        ["kubectl", "-n", NAMESPACE, "get", "pods", "-l", "app=es-cluster", "-o", "json"],
        errors,
        "pod list",
    )
    pods = {item.get("metadata", {}).get("name"): item for item in pods_data.get("items", [])} if pods_data else {}
    for ordinal in range(replicas):
        pod_name = f"{STS_NAME}-{ordinal}"
        pod = pods.get(pod_name)
        if not pod:
            errors.append(f"Missing pod {pod_name}")
            continue
        if not pod_ready(pod):
            errors.append(f"Pod {pod_name} is not Ready")

    if replicas >= 1:
        result = run(
            [
                "kubectl",
                "-n",
                NAMESPACE,
                "exec",
                f"{STS_NAME}-0",
                "--",
                "/bin/sh",
                "-c",
                f"test -f {MARKER_PATH}",
            ]
        )
        if result.returncode != 0:
            errors.append("Marker file missing on remaining pod")

    health = curl("/_cluster/health", errors)
    if isinstance(health, dict):
        status = health.get("status")
        if status not in {"yellow", "green"}:
            errors.append(f"Cluster health status expected yellow/green, got {status}")
        if health.get("number_of_nodes") != EXPECTED_REPLICAS:
            errors.append(
                f"Expected {EXPECTED_REPLICAS} node, got {health.get('number_of_nodes')}"
            )
        if health.get("unassigned_shards") not in (0, "0"):
            errors.append("Unassigned shards present after downscale")

    shards = curl("/_cat/shards/app-data?format=json", errors)
    if isinstance(shards, list):
        bad = [s for s in shards if s.get("node") != f"{STS_NAME}-0"]
        if bad:
            errors.append("app-data shards still present on removed nodes")
    else:
        errors.append("Unable to verify app-data shard placement")

    pvc_data = get_json(
        ["kubectl", "-n", NAMESPACE, "get", "pvc", "-o", "json"],
        errors,
        "PVC list",
    )
    pvc_names = []
    if pvc_data:
        pvc_names = [item.get("metadata", {}).get("name") for item in pvc_data.get("items", [])]
    prefixed = [name for name in pvc_names if name and name.startswith(PVC_PREFIX)]
    prefixed_set = set(prefixed)
    expected = {f"{PVC_PREFIX}{i}" for i in range(replicas)}
    missing = expected - prefixed_set
    for name in sorted(missing):
        errors.append(f"Missing PVC for active ordinal: {name}")
    for name in prefixed:
        ordinal = pvc_ordinal(name)
        if ordinal is None:
            continue
        if ordinal >= replicas:
            errors.append(f"Orphan PVC still present: {name}")

    if errors:
        print("Safe downscale verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Safe downscale verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
