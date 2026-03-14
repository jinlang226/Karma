import base64
import json
import re
import subprocess
import sys
import os

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")


def run(cmd):
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()


def run_json(cmd):
    return json.loads(run(cmd))


def main():
    errors = []

    try:
        pods = run_json([
            "kubectl", "-n", NAMESPACE, "get", "pods", "-l", f"app={CLUSTER_PREFIX}", "-o", "json"
        ])
    except subprocess.CalledProcessError as exc:
        print(f"Failed to list RabbitMQ pods: {exc.output.decode().strip()}")
        sys.exit(1)

    ready_pods = []
    for item in pods.get("items", []):
        name = item.get("metadata", {}).get("name", "unknown")
        phase = item.get("status", {}).get("phase")
        statuses = item.get("status", {}).get("containerStatuses", [])
        if phase != "Running" or not statuses or not all(s.get("ready") for s in statuses):
            errors.append(f"Pod not ready: {name}")
        else:
            ready_pods.append(name)

    if len(ready_pods) < 3:
        errors.append(f"Expected 3 RabbitMQ pods ready, got {len(ready_pods)}")

    if ready_pods:
        try:
            admin_secret = run_json([
                "kubectl", "-n", NAMESPACE, "get", "secret", f"{CLUSTER_PREFIX}-admin", "-o", "json"
            ])
            admin_user = base64.b64decode(
                admin_secret["data"]["username"]
            ).decode().strip()
            admin_pass = base64.b64decode(
                admin_secret["data"]["password"]
            ).decode().strip()
            nodes_raw = run([
                "kubectl", "-n", NAMESPACE, "exec", "oracle-client", "--",
                "/bin/sh", "-c",
                f"curl -s -u {admin_user}:{admin_pass} http://{CLUSTER_PREFIX}:15672/api/nodes"
            ])
            try:
                nodes = json.loads(nodes_raw)
            except json.JSONDecodeError:
                nodes = []
            if not isinstance(nodes, list):
                errors.append("Failed to query management API for nodes")
            else:
                if len(nodes) < 3:
                    errors.append("Cluster does not report 3 running nodes")
                names = {n.get("name") for n in nodes if isinstance(n, dict)}
                expected_nodes = [
                    f"rabbit@{pod}.{CLUSTER_PREFIX}-headless.{NAMESPACE}.svc.cluster.local"
                    for pod in ready_pods
                ]
                for node in expected_nodes:
                    if node not in names:
                        errors.append(f"Cluster missing running node: {node}")
        except (subprocess.CalledProcessError, KeyError) as exc:
            detail = exc.output.decode().strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            errors.append(f"Failed to read cluster membership: {detail}")

    # Erlang cookie consistency check
    try:
        secret = run_json([
            "kubectl", "-n", NAMESPACE, "get", "secret", f"{CLUSTER_PREFIX}-cookie-perpod", "-o", "json"
        ])
        data = secret.get("data", {})
        cookies = {}
        for key, value in data.items():
            try:
                cookies[key] = base64.b64decode(value).decode().strip()
            except Exception:
                cookies[key] = None
        missing = [k for k in tuple(f"{CLUSTER_PREFIX}-{i}" for i in range(3)) if k not in cookies]
        if missing:
            errors.append(f"Missing cookie keys in secret: {', '.join(missing)}")
        else:
            values = [cookies[f"{CLUSTER_PREFIX}-0"], cookies[f"{CLUSTER_PREFIX}-1"], cookies[f"{CLUSTER_PREFIX}-2"]]
            if any(v is None or v == "" for v in values):
                errors.append("Erlang cookie values are empty or unreadable")
            elif len(set(values)) != 1:
                errors.append("Erlang cookie values are not consistent across nodes")
    except subprocess.CalledProcessError as exc:
        errors.append(f"Failed to read cookie secret: {exc.output.decode().strip()}")

    if errors:
        print("RabbitMQ failover verification failed:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print("RabbitMQ failover verified.")


if __name__ == "__main__":
    main()
