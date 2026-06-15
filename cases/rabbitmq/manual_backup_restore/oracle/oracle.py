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


def _resolve_expected_nodes():
    """Cluster size to enforce: param override -> live StatefulSet -> default 3.

    The env PERSISTS across stages, so a prior scale stage may have grown the
    cluster past the standalone default of 3. Only the count target adapts; the
    cluster_status membership check still fails for any unready/dropped node.
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


def main():
    errors = []
    expected_nodes = _resolve_expected_nodes()

    try:
        pods = run_json([
            "kubectl", "-n", NAMESPACE, "get", "pods", "-l", f"app={CLUSTER_PREFIX}", "-o", "json"
        ])
    except subprocess.CalledProcessError as exc:
        print(f"Failed to list RabbitMQ pods: {exc.output.decode().strip()}")
        sys.exit(1)

    ready = []
    for item in pods.get("items", []):
        name = item.get("metadata", {}).get("name", "unknown")
        phase = item.get("status", {}).get("phase")
        statuses = item.get("status", {}).get("containerStatuses", [])
        if phase != "Running" or not statuses or not all(s.get("ready") for s in statuses):
            errors.append(f"Pod not ready: {name}")
        else:
            ready.append(name)

    if len(ready) != expected_nodes:
        errors.append(f"Expected {expected_nodes} RabbitMQ pods ready, got {len(ready)}")

    if ready:
        try:
            cluster_out = run([
                "kubectl", "-n", NAMESPACE, "exec", ready[0], "--",
                "rabbitmqctl", "cluster_status"
            ])
            running = re.findall(r"rabbit@[^\s,\]\}]+", cluster_out)
            if len(set(running)) < expected_nodes:
                errors.append(f"Cluster does not report {expected_nodes} running nodes")
        except subprocess.CalledProcessError as exc:
            errors.append(f"Failed to read cluster status: {exc.output.decode().strip()}")

    if ready:
        try:
            queues = run([
                "kubectl", "-n", NAMESPACE, "exec", ready[0], "--",
                "rabbitmqctl", "-q", "list_queues", "-p", "/app", "name", "messages"
            ])
            found = False
            for line in queues.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0] == "app-backup":
                    found = True
                    try:
                        messages = int(parts[1])
                    except ValueError:
                        messages = 0
                    if messages < 20:
                        errors.append("app-backup does not have expected messages (>=20)")
                    break
            if not found:
                errors.append("app-backup queue not found in /app")
        except subprocess.CalledProcessError as exc:
            errors.append(f"Failed to list queues in /app: {exc.output.decode().strip()}")

    if errors:
        print("RabbitMQ backup/restore verification failed:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print("RabbitMQ backup/restore verified.")


if __name__ == "__main__":
    main()
