#!/usr/bin/env python3
import json
import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "es-cluster")
BUNDLE_CONFIGMAP = os.environ.get("BENCH_PARAM_TRANSPORT_BUNDLE_CONFIGMAP", "es-transport-ca-bundle")


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
        "/bin/sh",
        "-c",
        (
            "curl -s -S --max-time 10 -u \"$ES_USER:$ES_PASS\" "
            f"http://{SERVICE}:9200{path}"
        ),
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
    expected = expected_nodes(errors)

    cm_result = run(["kubectl", "-n", NAMESPACE, "get", "configmap", BUNDLE_CONFIGMAP, "-o", "json"])
    if cm_result.returncode != 0:
        errors.append(f"Failed to read {BUNDLE_CONFIGMAP} ConfigMap: {cm_result.stderr.strip()}")
    else:
        try:
            cm = json.loads(cm_result.stdout)
        except json.JSONDecodeError:
            errors.append(f"Failed to parse {BUNDLE_CONFIGMAP} ConfigMap JSON")
            cm = {}
        bundle = cm.get("data", {}).get("ca.crt", "")
        cert_count = bundle.count("BEGIN CERTIFICATE")
        if cert_count < 2:
            errors.append(f"Transport CA bundle should contain 2 certs, found {cert_count}")

    health = curl(
        "/_cluster/health?wait_for_status=yellow&timeout=30s",
        errors,
    )
    if isinstance(health, dict):
        if health.get("status") not in {"yellow", "green"}:
            errors.append(f"Cluster health status expected yellow/green, got {health.get('status')}")
        if expected is not None and health.get("number_of_nodes") != expected:
            errors.append(f"Expected {expected} nodes, got {health.get('number_of_nodes')}")

    nodes = curl("/_cat/nodes?format=json", errors)
    if isinstance(nodes, list) and expected is not None and len(nodes) != expected:
        errors.append(f"Expected {expected} nodes in _cat/nodes, got {len(nodes)}")

    if errors:
        print("Transport CA trust verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Transport CA trust verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
