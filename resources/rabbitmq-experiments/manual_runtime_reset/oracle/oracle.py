#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")
CANONICAL_QUEUE = os.environ.get("BENCH_PARAM_CANONICAL_QUEUE", "app-queue")
STALE_VHOST = os.environ.get("BENCH_PARAM_STALE_VHOST", "stale")
STALE_USER = os.environ.get("BENCH_PARAM_STALE_USER", "stale-user")
STALE_POLICY = os.environ.get("BENCH_PARAM_STALE_POLICY", "stale-policy")


def run(cmd):
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode()


def run_json(cmd):
    return json.loads(run(cmd))


def _list_lines(cmd):
    return [line.strip() for line in run(cmd).splitlines() if line.strip()]


def check_pods_ready():
    pods = run_json(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "pods",
            "-l",
            f"app={CLUSTER_PREFIX}",
            "-o",
            "json",
        ]
    )
    items = pods.get("items", [])
    if len(items) != 3:
        return f"expected 3 RabbitMQ pods, found {len(items)}"
    for item in items:
        name = (item.get("metadata") or {}).get("name", "unknown")
        phase = (item.get("status") or {}).get("phase")
        statuses = (item.get("status") or {}).get("containerStatuses") or []
        if phase != "Running" or not statuses or not all(s.get("ready") for s in statuses):
            return f"pod not ready: {name}"
    return None


def check_statefulset_present():
    try:
        run(["kubectl", "-n", NAMESPACE, "get", "sts", CLUSTER_PREFIX])
    except subprocess.CalledProcessError as exc:
        return f"statefulset missing: {exc.output.decode().strip()}"
    return None


def check_stale_vhost_absent():
    vhosts = set(
        _list_lines(
            [
                "kubectl",
                "-n",
                NAMESPACE,
                "exec",
                f"{CLUSTER_PREFIX}-0",
                "--",
                "rabbitmqctl",
                "-q",
                "list_vhosts",
                "name",
            ]
        )
    )
    if STALE_VHOST in vhosts:
        return f"stale vhost {STALE_VHOST!r} still present"
    return None


def check_stale_user_absent():
    users = _list_lines(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            f"{CLUSTER_PREFIX}-0",
            "--",
            "rabbitmqctl",
            "-q",
            "list_users",
        ]
    )
    for line in users:
        parts = line.split()
        if parts and parts[0] == STALE_USER:
            return f"stale user {STALE_USER!r} still present"
    return None


def check_stale_policy_absent():
    out = _list_lines(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            f"{CLUSTER_PREFIX}-0",
            "--",
            "rabbitmqctl",
            "list_policies",
            "-p",
            "/app",
        ]
    )
    for line in out:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == STALE_POLICY:
            return f"stale policy {STALE_POLICY!r} still present on /app"
    return None


def _queue_messages(vhost, queue_name):
    lines = _list_lines(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            f"{CLUSTER_PREFIX}-0",
            "--",
            "rabbitmqctl",
            "-q",
            "list_queues",
            "-p",
            vhost,
            "name",
            "messages",
        ]
    )
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == queue_name:
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def check_canonical_queue_exists():
    count = _queue_messages("/app", CANONICAL_QUEUE)
    if count is None:
        return f"canonical queue /app/{CANONICAL_QUEUE} missing"
    return None


def check_canonical_queue_empty():
    count = _queue_messages("/app", CANONICAL_QUEUE)
    if count is None:
        return f"canonical queue /app/{CANONICAL_QUEUE} missing"
    if count != 0:
        return f"canonical queue /app/{CANONICAL_QUEUE} has {count} messages; expected 0"
    return None


CHECKS = {
    "pods_ready": check_pods_ready,
    "statefulset_present": check_statefulset_present,
    "stale_vhost_absent": check_stale_vhost_absent,
    "stale_user_absent": check_stale_user_absent,
    "stale_policy_absent": check_stale_policy_absent,
    "canonical_queue_exists": check_canonical_queue_exists,
    "canonical_queue_empty": check_canonical_queue_empty,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="append",
        choices=sorted(CHECKS.keys()),
        help="Run only selected checks (can be repeated). Defaults to all checks.",
    )
    args = parser.parse_args()

    selected = args.check or list(CHECKS.keys())
    failed = []

    for name in selected:
        fn = CHECKS[name]
        try:
            err = fn()
        except subprocess.CalledProcessError as exc:
            err = exc.output.decode().strip() or str(exc)
        except Exception as exc:
            err = str(exc)
        if err:
            failed.append(f"{name}: {err}")

    if failed:
        print("Manual runtime reset verification failed:")
        for item in failed:
            print(f"  - {item}")
        return 1

    print("Manual runtime reset verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
