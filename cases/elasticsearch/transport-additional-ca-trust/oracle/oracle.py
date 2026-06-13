#!/usr/bin/env python3
import json
import subprocess
import sys


NAMESPACE = "elasticsearch"
SERVICE = "es-http"
EXPECTED_NODES = 3


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
        (
            "curl -s -S --max-time 10 -u \"$ES_USER:$ES_PASS\" "
            f"http://{SERVICE}.{NAMESPACE}.svc:9200{path}"
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

    cm_result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "configmap",
            "es-transport-ca-bundle",
            "-o",
            "json",
        ]
    )
    if cm_result.returncode != 0:
        errors.append(
            f"Failed to read es-transport-ca-bundle ConfigMap: {cm_result.stderr.strip()}"
        )
    else:
        try:
            cm = json.loads(cm_result.stdout)
        except json.JSONDecodeError:
            errors.append("Failed to parse es-transport-ca-bundle ConfigMap JSON")
            cm = {}
        bundle = cm.get("data", {}).get("ca.crt", "")
        cert_count = bundle.count("BEGIN CERTIFICATE")
        if cert_count < 2:
            errors.append(
                f"Transport CA bundle should contain 2 certs, found {cert_count}"
            )

    health = curl(
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

    nodes = curl("/_cat/nodes?format=json", errors)
    if isinstance(nodes, list):
        if len(nodes) != EXPECTED_NODES:
            errors.append(f"Expected {EXPECTED_NODES} nodes in _cat/nodes, got {len(nodes)}")

    if errors:
        print("Transport CA trust verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Transport CA trust verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
