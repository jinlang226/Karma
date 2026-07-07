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


def curl_from_curl_test(url):
    return run([
        "kubectl", "-n", NAMESPACE, "exec", "oracle-client", "--",
        "curl", "-s", "--connect-timeout", "5", "--max-time", "25", url
    ])


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


def evaluate():
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
    pod_names = []
    for item in pods.get("items", []):
        name = item.get("metadata", {}).get("name", "unknown")
        pod_names.append(name)
        phase = item.get("status", {}).get("phase")
        statuses = item.get("status", {}).get("containerStatuses", [])
        if phase != "Running" or not statuses or not all(s.get("ready") for s in statuses):
            errors.append(f"Pod not ready: {name}")
        else:
            ready_pods += 1

    if ready_pods < expected_nodes:
        errors.append(f"Expected {expected_nodes} RabbitMQ pods ready, got {ready_pods}")

    for name in pod_names:
        try:
            out = curl_from_curl_test(
                f"http://{name}.{CLUSTER_PREFIX}-headless.{NAMESPACE}.svc.cluster.local:15692/metrics"
            )
            if "rabbitmq_" not in out:
                errors.append(f"RabbitMQ metrics missing on {name}")
        except subprocess.CalledProcessError as exc:
            errors.append(f"Failed to fetch metrics from {name}: {exc.output.decode().strip()}")

    try:
        targets_raw = curl_from_curl_test(
            f"http://prometheus.{NAMESPACE}.svc.cluster.local:8000/api/v1/targets"
        )
        targets = json.loads(targets_raw)
        if targets.get("status") != "success":
            errors.append("Prometheus targets query failed")
            targets = {}
    except Exception as exc:
        errors.append(f"Failed to query Prometheus targets: {exc}")
        targets = {}

    rabbit_targets = []
    for target in targets.get("data", {}).get("activeTargets", []):
        if target.get("labels", {}).get("job") == "rabbitmq":
            rabbit_targets.append(target)

    if len(rabbit_targets) < expected_nodes:
        errors.append(f"Expected {expected_nodes} RabbitMQ targets in Prometheus, got {len(rabbit_targets)}")
    else:
        down = [t for t in rabbit_targets if t.get("health") != "up"]
        if down:
            errors.append(f"Prometheus has {len(down)} RabbitMQ targets not UP")

    try:
        query_raw = curl_from_curl_test(
            f"http://prometheus.{NAMESPACE}.svc.cluster.local:8000/api/v1/query?query=up%7Bjob%3D%22rabbitmq%22%7D"
        )
        query = json.loads(query_raw)
        if query.get("status") != "success":
            errors.append("Prometheus query up{job=\"rabbitmq\"} failed")
        else:
            results = query.get("data", {}).get("result", [])
            if len(results) < expected_nodes:
                errors.append(f"Expected {expected_nodes} up{{job=\"rabbitmq\"}} samples, got {len(results)}")
            for sample in results:
                value = sample.get("value", [None, "0"])[1]
                if value not in ("1", "1.0"):
                    errors.append("up{job=\"rabbitmq\"} has non-1 value")
                    break
    except Exception as exc:
        errors.append(f"Failed to query up{{job=\"rabbitmq\"}}: {exc}")

    return errors


def main():
    import time
    deadline = time.monotonic() + 120
    errors = evaluate()
    while errors and time.monotonic() < deadline:
        time.sleep(8)
        errors = evaluate()

    if errors:
        print("RabbitMQ monitoring verification failed:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print("RabbitMQ monitoring verified.")


if __name__ == "__main__":
    main()
