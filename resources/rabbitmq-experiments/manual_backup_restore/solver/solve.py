#!/usr/bin/env python3
import os
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from solver_utils import run, wait_statefulset_ready, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")


def backup_queue_restored():
    out = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            "rabbitmq-0",
            "--",
            "rabbitmqctl",
            "-q",
            "list_queues",
            "-p",
            "/app",
            "name",
            "messages",
        ]
    )
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == "app-backup":
            try:
                return int(parts[1]) >= 20
            except ValueError:
                return False
    return False


def main():
    run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "delete",
            "job",
            "rabbitmq-restore",
            "--ignore-not-found=true",
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "apply",
            "-f",
            "resources/rabbitmq-experiments/manual_backup_restore/resource/job-restore.yaml",
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "wait",
            "--for=condition=complete",
            "job/rabbitmq-restore",
            "--timeout=300s",
        ]
    )

    run(["kubectl", "-n", NAMESPACE, "scale", "sts/rabbitmq", "--replicas=3"])
    wait_statefulset_ready(NAMESPACE, "rabbitmq", timeout_sec=900)
    wait_until(
        backup_queue_restored,
        timeout_sec=180,
        interval_sec=5,
        description="app-backup queue restored with messages",
    )
    print("manual_backup_restore solver applied")


if __name__ == "__main__":
    main()
