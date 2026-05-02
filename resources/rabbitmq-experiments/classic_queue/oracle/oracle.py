import base64
import json
import subprocess
import sys
import os

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")


def run(cmd):
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()


def run_json(cmd):
    return json.loads(run(cmd))


def get_secret_value(name, key):
    data = run_json([
        "kubectl", "-n", NAMESPACE, "get", "secret", name, "-o", "json"
    ])
    raw = data.get("data", {}).get(key)
    if not raw:
        raise ValueError(f"Missing secret data: {name}/{key}")
    return base64.b64decode(raw).decode().strip()


def curl_api(path, user, password):
    return run([
        "kubectl", "-n", NAMESPACE, "exec", "oracle-client", "--",
        "curl", "-s", "-u", f"{user}:{password}",
        f"http://{CLUSTER_PREFIX}:15672{path}",
    ])


def main():
    errors = []

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

    if ready_pods < 3:
        errors.append(f"Expected 3 RabbitMQ pods ready, got {ready_pods}")

    try:
        dep = run_json([
            "kubectl", "-n", NAMESPACE, "get", "deployment", "app-producer", "-o", "json"
        ])
        ready_repl = dep.get("status", {}).get("readyReplicas", 0)
        if ready_repl < 1:
            errors.append("app-producer is not Ready")
    except subprocess.CalledProcessError as exc:
        errors.append(f"Failed to read app-producer deployment: {exc.output.decode().strip()}")

    try:
        admin_user = get_secret_value(f"{CLUSTER_PREFIX}-admin", "username")
        admin_pass = get_secret_value(f"{CLUSTER_PREFIX}-admin", "password")
    except Exception as exc:
        errors.append(f"Failed to read admin credentials: {exc}")
        admin_user = None
        admin_pass = None

    if admin_user and admin_pass:
        try:
            nodes = json.loads(curl_api("/api/nodes", admin_user, admin_pass))
            if len(nodes) < 3:
                errors.append(f"Expected 3 nodes in cluster, got {len(nodes)}")
        except Exception:
            errors.append("Failed to query cluster nodes via management API")

        try:
            policies = json.loads(curl_api("/api/policies/%2Fapp", admin_user, admin_pass))
            for policy in policies:
                definition = policy.get("definition", {})
                if definition.get("queue-type") == "quorum":
                    errors.append("Quorum queue policy still present on /app")
                    break
        except Exception:
            errors.append("Failed to query policies for /app")

        try:
            perms = json.loads(curl_api("/api/permissions/%2Fapp/app-user", admin_user, admin_pass))
            configure = perms.get("configure", "")
            if not configure:
                errors.append("app-user configure permission for /app is empty")
        except Exception:
            errors.append("Failed to query app-user permissions for /app")

        try:
            queue_info = json.loads(curl_api("/api/queues/%2Fapp/app-queue", admin_user, admin_pass))
            messages = queue_info.get("messages", 0)
            if messages < 1:
                errors.append("app-queue has no messages")
        except Exception:
            errors.append("Failed to query app-queue state")

    try:
        queue_out = run([
            "kubectl", "-n", NAMESPACE, "exec", f"{CLUSTER_PREFIX}-0", "--",
            "rabbitmqctl", "-q", "list_queues", "-p", "/app", "name", "type"
        ])
        found = False
        for line in queue_out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == "app-queue":
                found = True
                if parts[1] != "classic":
                    errors.append("app-queue is not a classic queue")
                break
        if not found:
            errors.append("app-queue not found in /app")
    except subprocess.CalledProcessError as exc:
        errors.append(f"Failed to list queues in /app: {exc.output.decode().strip()}")

    if errors:
        print("Classic queue verification failed:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print("Classic queue verified.")


if __name__ == "__main__":
    main()
