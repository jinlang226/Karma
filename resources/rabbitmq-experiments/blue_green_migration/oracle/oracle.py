import base64
import json
import subprocess
import sys
import os

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
SOURCE_NAMESPACE = os.environ.get("BENCH_NS_SOURCE", NAMESPACE)
TARGET_NAMESPACE = os.environ.get("BENCH_NS_TARGET", NAMESPACE)
BLUE_CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_BLUE_CLUSTER_PREFIX", "rabbitmq-blue")
GREEN_CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_GREEN_CLUSTER_PREFIX", "rabbitmq-green")


def run(cmd):
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()


def run_json(cmd):
    return json.loads(run(cmd))


def get_secret_value(name, key):
    data = run_json([
        "kubectl", "-n", TARGET_NAMESPACE, "get", "secret", name, "-o", "json"
    ])
    raw = data.get("data", {}).get(key)
    if not raw:
        raise ValueError(f"Missing secret data: {name}/{key}")
    return base64.b64decode(raw).decode().strip()


def curl_green(path, method="GET", data=None):
    args = ["kubectl", "-n", TARGET_NAMESPACE, "exec", "oracle-client", "--", "curl", "-s", "-u", "admin:adminpass"]
    if method == "POST":
        args += ["-H", "content-type: application/json", "-X", "POST"]
        if data is not None:
            args += ["-d", json.dumps(data)]
    args.append(f"http://{GREEN_CLUSTER_PREFIX}:15672{path}")
    return run(args)


def evaluate_green_batch(seed_count, batch):
    errors = []
    if len(batch) < seed_count:
        errors.append(f"Expected at least {seed_count} messages on green, got {len(batch)}")
        return errors

    found_ids = set()
    live_ids = set()
    for msg in batch:
        payload = msg.get("payload", "")
        try:
            parsed = json.loads(payload)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        raw_id = parsed.get("id")
        try:
            msg_id = int(raw_id)
        except Exception:
            continue
        if 1 <= msg_id <= seed_count:
            found_ids.add(msg_id)
        elif msg_id > seed_count:
            live_ids.add(msg_id)

    if len(found_ids) < seed_count:
        missing = [str(i) for i in range(1, seed_count + 1) if i not in found_ids]
        preview = ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else "")
        errors.append(f"Seed range 1..N not fully present on green (missing: {preview})")

    if not live_ids:
        errors.append(
            f"Expected at least one live post-seed message on green (id > {seed_count}) to prove cutover"
        )

    return errors


def main():
    errors = []

    for label in (BLUE_CLUSTER_PREFIX, GREEN_CLUSTER_PREFIX):
        try:
            ns = SOURCE_NAMESPACE if label == BLUE_CLUSTER_PREFIX else TARGET_NAMESPACE
            pods = run_json(["kubectl", "-n", ns, "get", "pods", "-l", f"app={label}", "-o", "json"])
        except subprocess.CalledProcessError as exc:
            errors.append(f"Failed to list {label} pods: {exc.output.decode().strip()}")
            pods = {"items": []}

        ready = 0
        for item in pods.get("items", []):
            name = item.get("metadata", {}).get("name", "unknown")
            phase = item.get("status", {}).get("phase")
            statuses = item.get("status", {}).get("containerStatuses", [])
            if phase != "Running" or not statuses or not all(s.get("ready") for s in statuses):
                errors.append(f"Pod not ready: {name}")
            else:
                ready += 1
        if ready < 3:
            errors.append(f"Expected 3 {label} pods ready, got {ready}")

    try:
        seed_cfg = run_json([
            "kubectl", "-n", TARGET_NAMESPACE, "get", "configmap", "migration-seed", "-o", "json"
        ])
        seed_count = int(seed_cfg.get("data", {}).get("seed_count", "0"))
    except Exception as exc:
        errors.append(f"Failed to read migration seed count from target namespace: {exc}")
        seed_count = 0

    if seed_count <= 0:
        errors.append("Seed count is missing or invalid")

    try:
        queue_info = json.loads(curl_green("/api/queues/%2Fapp/app-queue"))
        if not queue_info.get("name"):
            errors.append("app-queue not found on green")
    except Exception:
        errors.append("Failed to query green app-queue")

    if seed_count > 0:
        try:
            fetch_count = max(seed_count + 20, seed_count * 3)
            batch = json.loads(curl_green(
                "/api/queues/%2Fapp/app-queue/get",
                method="POST",
                data={
                    "count": fetch_count,
                    "ackmode": "ack_requeue_true",
                    "encoding": "auto",
                    "truncate": 50000,
                },
            ))
            errors.extend(evaluate_green_batch(seed_count, batch))
        except Exception as exc:
            errors.append(f"Failed to fetch seed batch from green: {exc}")

    if errors:
        print("Blue/green migration verification failed:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)

    print("Blue/green migration verified.")


if __name__ == "__main__":
    main()
