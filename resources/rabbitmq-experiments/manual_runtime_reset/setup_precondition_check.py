#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[1] / "common"
sys.path.insert(0, str(COMMON))

from setup_check_utils import expect_pods_ready, run, split_lines  # noqa: E402


CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")
CANONICAL_QUEUE = os.environ.get("BENCH_PARAM_CANONICAL_QUEUE", "app-queue")
STALE_VHOST = os.environ.get("BENCH_PARAM_STALE_VHOST", "stale")
STALE_USER = os.environ.get("BENCH_PARAM_STALE_USER", "stale-user")
STALE_POLICY = os.environ.get("BENCH_PARAM_STALE_POLICY", "stale-policy")
STALE_QUEUE = os.environ.get("BENCH_PARAM_STALE_QUEUE", "stale-queue")
SEED_MESSAGE_COUNT = int(os.environ.get("BENCH_PARAM_SEED_MESSAGE_COUNT", "50"))


def _check_cluster(namespace, errors):
    expect_pods_ready(namespace, f"app={CLUSTER_PREFIX}", 3, errors, CLUSTER_PREFIX)
    try:
        run(["kubectl", "-n", namespace, "get", "sts", CLUSTER_PREFIX])
    except Exception as exc:
        errors.append(f"statefulset {CLUSTER_PREFIX} missing: {exc}")


def _has_vhost(namespace, vhost):
    out = run(
        [
            "kubectl",
            "-n",
            namespace,
            "exec",
            f"{CLUSTER_PREFIX}-0",
            "--",
            "rabbitmqctl",
            "-q",
            "list_vhosts",
            "name",
        ]
    )
    return vhost in set(split_lines(out))


def _has_user(namespace, username):
    out = run(
        [
            "kubectl",
            "-n",
            namespace,
            "exec",
            f"{CLUSTER_PREFIX}-0",
            "--",
            "rabbitmqctl",
            "-q",
            "list_users",
        ]
    )
    for line in split_lines(out):
        token = line.split()[0] if line.split() else ""
        if token == username:
            return True
    return False


def _has_policy(namespace, vhost, policy_name):
    out = run(
        [
            "kubectl",
            "-n",
            namespace,
            "exec",
            f"{CLUSTER_PREFIX}-0",
            "--",
            "rabbitmqctl",
            "list_policies",
            "-p",
            vhost,
        ]
    )
    for line in split_lines(out):
        parts = line.split()
        if len(parts) >= 2 and parts[1] == policy_name:
            return True
    return False


def _queue_messages(namespace, vhost, queue_name):
    out = run(
        [
            "kubectl",
            "-n",
            namespace,
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
    for line in split_lines(out):
        parts = line.split()
        if len(parts) >= 2 and parts[0] == queue_name:
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def _check_runtime_drift(namespace, errors):
    try:
        if not _has_vhost(namespace, STALE_VHOST):
            errors.append(f"stale vhost {STALE_VHOST!r} missing")
        if not _has_user(namespace, STALE_USER):
            errors.append(f"stale user {STALE_USER!r} missing")
        if not _has_policy(namespace, "/app", STALE_POLICY):
            errors.append(f"stale policy {STALE_POLICY!r} missing on /app")
        stale_queue_count = _queue_messages(namespace, STALE_VHOST, STALE_QUEUE)
        if stale_queue_count is None:
            errors.append(f"stale queue {STALE_VHOST}/{STALE_QUEUE} missing")
        canonical_count = _queue_messages(namespace, "/app", CANONICAL_QUEUE)
        if canonical_count is None:
            errors.append(f"canonical queue /app/{CANONICAL_QUEUE} missing")
        elif canonical_count < SEED_MESSAGE_COUNT:
            errors.append(
                f"canonical queue /app/{CANONICAL_QUEUE} has {canonical_count}, expected >= {SEED_MESSAGE_COUNT}"
            )
    except Exception as exc:
        errors.append(f"failed to inspect runtime drift state: {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default=os.environ.get("BENCH_NAMESPACE", "rabbitmq"))
    parser.add_argument("--cluster-only", action="store_true")
    parser.add_argument("--drift-only", action="store_true")
    args = parser.parse_args()

    errors = []
    _check_cluster(args.namespace, errors)

    if args.cluster_only:
        if errors:
            print("setup-precondition-check: failed")
            for err in errors:
                print(f" - {err}")
            return 1
        print("setup-precondition-check: ok")
        return 0

    if args.drift_only or not args.cluster_only:
        _check_runtime_drift(args.namespace, errors)

    if errors:
        print("setup-precondition-check: failed")
        for err in errors:
            print(f" - {err}")
        return 1
    print("setup-precondition-check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
