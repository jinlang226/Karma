import json
import subprocess
import sys
import os

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")


def run(cmd):
    return subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=True, timeout=60,
    ).stdout.decode()


def run_json(cmd):
    return json.loads(run(cmd))


def _resolve_expected_nodes():
    try:
        sts = run_json(["kubectl", "-n", NAMESPACE, "get", "sts", CLUSTER_PREFIX, "-o", "json"])
        status = sts.get("status", {}) or {}
        spec = sts.get("spec", {}) or {}
        live = status.get("readyReplicas")
        if not isinstance(live, int) or live <= 0:
            live = spec.get("replicas")
        if isinstance(live, int) and live > 0:
            return live
    except Exception:
        pass
    return 3


def main():
    errors = []
    expected_nodes = _resolve_expected_nodes()

    try:
        pods = run_json([
            "kubectl", "-n", NAMESPACE, "get", "pods", "-l", f"app={CLUSTER_PREFIX}", "-o", "json"
        ])
    except subprocess.CalledProcessError as exc:
        errors.append(f"Failed to list RabbitMQ pods: {exc.output.decode().strip()}")
        pods = {"items": []}

    ready_pods = 0
    for item in pods.get("items", []):
        name = item.get("metadata", {}).get("name", "unknown")
        phase = item.get("status", {}).get("phase")
        statuses = item.get("status", {}).get("containerStatuses", [])
        if phase != "Running" or not statuses or not all(s.get("ready") for s in statuses):
            errors.append(f"Pod not ready: {name}")
        else:
            ready_pods += 1

    if ready_pods < expected_nodes:
        errors.append(f"Expected {expected_nodes} RabbitMQ pods ready, got {ready_pods}")

    try:
        policies = run([
            "kubectl", "-n", NAMESPACE, "exec", f"{CLUSTER_PREFIX}-0", "--",
            "rabbitmqctl", "list_policies", "-p", "/app"
        ])
        if "ha-all" not in policies:
            errors.append("Mirroring policy 'ha-all' not found on /app")
    except subprocess.CalledProcessError as exc:
        errors.append(f"Failed to list policies for /app: {exc.output.decode().strip()}")

    try:
        queue_out = run([
            "kubectl", "-n", NAMESPACE, "exec", f"{CLUSTER_PREFIX}-0", "--",
            "rabbitmqctl", "-q", "list_queues", "-p", "/app", "name", "type", "policy"
        ])
        found = False
        for line in queue_out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 3 and parts[0] == "app-queue":
                found = True
                if parts[1] != "classic":
                    errors.append("app-queue is not a classic queue")
                if parts[2] != "ha-all":
                    errors.append("app-queue does not have policy ha-all")
                break
        if not found:
            errors.append("app-queue not found in /app")
    except subprocess.CalledProcessError as exc:
        errors.append(f"Failed to list queues in /app: {exc.output.decode().strip()}")

    if errors:
        print("Manual policy sync verification failed:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print("Manual policy sync verified.")


if __name__ == "__main__":
    main()
