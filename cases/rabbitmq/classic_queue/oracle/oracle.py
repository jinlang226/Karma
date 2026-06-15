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


_MGMT_BASE = None


def mgmt_base():
    """Resolve the live management-API base URL (scheme + port).

    Standalone this cluster serves the management plugin over plain HTTP on
    15672. A prior workflow stage may have enabled TLS on the management
    listener (HTTPS on 15671); a hardcoded http://:15672 then fails. Probe the
    live cluster: try https://:15671 first (with -k), fall back to
    http://:15672. Auth is NOT bypassed -- only the transport scheme adapts.
    """
    global _MGMT_BASE
    if _MGMT_BASE is not None:
        return _MGMT_BASE
    candidates = [f"https://{CLUSTER_PREFIX}:15671", f"http://{CLUSTER_PREFIX}:15672"]
    for base in candidates:
        try:
            out = subprocess.run(
                [
                    "kubectl", "-n", NAMESPACE, "exec", "oracle-client", "--",
                    "curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}",
                    f"{base}/api/overview",
                ],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            ).stdout.strip()
            if out and out[:1].isdigit() and out != "000":
                _MGMT_BASE = base
                return _MGMT_BASE
        except Exception:
            continue
    _MGMT_BASE = candidates[-1]
    return _MGMT_BASE


def _resolve_expected_nodes():
    """Cluster size to enforce: param override -> live StatefulSet -> default 3.

    The env PERSISTS across stages, so a prior scale stage may have grown the
    cluster past the standalone default of 3. Only the count target adapts; the
    per-node and queue checks below still fail for any unready/dropped member.
    """
    for key in ("BENCH_PARAM_EXPECTED_NODES", "BENCH_PARAM_TARGET_NODES"):
        val = os.environ.get(key)
        if val is not None and str(val).strip():
            try:
                return int(val)
            except ValueError:
                pass
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


def curl_api(path, user, password):
    return run([
        "kubectl", "-n", NAMESPACE, "exec", "oracle-client", "--",
        "curl", "-sk", "-u", f"{user}:{password}",
        f"{mgmt_base()}{path}",
    ])


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
            if len(nodes) < expected_nodes:
                errors.append(f"Expected {expected_nodes} nodes in cluster, got {len(nodes)}")
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
