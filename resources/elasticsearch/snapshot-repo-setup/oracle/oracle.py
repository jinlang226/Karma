#!/usr/bin/env python3
import json
import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
EXPECTED_NODES = int(os.environ.get("BENCH_PARAM_EXPECTED_NODE_COUNT", "3"))
REPO_NAME = os.environ.get("BENCH_PARAM_SNAPSHOT_REPO_NAME", "minio-repo")
KEYS = {
    "s3.client.default.access_key",
    "s3.client.default.secret_key",
}


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


def get_pods(errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "pods",
            "-l",
            "app=es-cluster",
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
    return [item.get("metadata", {}).get("name") for item in payload.get("items", [])]


def check_keystore(pod, errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            pod,
            "--",
            "/usr/share/elasticsearch/bin/elasticsearch-keystore",
            "list",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        errors.append(f"Failed to list keystore on {pod}: {detail}")
        return
    keys = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    missing = sorted(KEYS - keys)
    if missing:
        errors.append(f"Missing keystore keys on {pod}: {', '.join(missing)}")


def check_configmap(errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "configmap",
            "es-config",
            "-o",
            "json",
        ]
    )
    if result.returncode != 0:
        errors.append(f"Failed to read es-config ConfigMap: {result.stderr.strip()}")
        return
    try:
        cm = json.loads(result.stdout)
    except json.JSONDecodeError:
        errors.append("Failed to parse es-config ConfigMap JSON")
        return
    text = json.dumps(cm)
    if "access_key" in text or "secret_key" in text:
        errors.append("Plaintext credentials found in es-config ConfigMap")


def check_snapshots(errors):
    repo = curl(f"/_snapshot/{REPO_NAME}", errors)
    if repo is None:
        return
    if "error" in repo:
        errors.append(f"Snapshot repository {REPO_NAME} not found")
        return

    snaps = curl(f"/_snapshot/{REPO_NAME}/_all", errors)
    if not isinstance(snaps, dict):
        return
    snapshots = snaps.get("snapshots", [])
    if not snapshots:
        errors.append("No snapshots found in repository")
        return
    success = [s for s in snapshots if s.get("state") == "SUCCESS"]
    if not success:
        errors.append("No SUCCESS snapshots found in repository")


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
            errors.append(
                f"Expected {EXPECTED_NODES} nodes, got {health.get('number_of_nodes')}"
            )

    check_snapshots(errors)
    check_configmap(errors)

    pods = get_pods(errors)
    if pods:
        for pod in pods:
            check_keystore(pod, errors)
    else:
        errors.append("No Elasticsearch pods found to verify keystore")

    if errors:
        print("Secure settings verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Secure settings verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
