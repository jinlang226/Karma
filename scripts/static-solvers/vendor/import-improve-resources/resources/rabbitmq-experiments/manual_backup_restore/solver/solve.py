#!/usr/bin/env python3
import os
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from solver_utils import apply_yaml, run, wait_statefulset_ready, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")
SEED_POD = f"{CLUSTER_PREFIX}-0"


def render_restore_job_yaml():
    template = Path(
        "resources/rabbitmq-experiments/manual_backup_restore/resource/job-restore.yaml"
    ).read_text(encoding="utf-8")
    return template.replace("${BENCH_PARAM_CLUSTER_PREFIX}", CLUSTER_PREFIX)


def backup_queue_restored():
    out = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            SEED_POD,
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
            "delete",
            "pod",
            "-l",
            "job-name=rabbitmq-restore",
            "--ignore-not-found=true",
        ]
    )
    apply_yaml(render_restore_job_yaml(), namespace=NAMESPACE)
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

    run(["kubectl", "-n", NAMESPACE, "scale", f"sts/{CLUSTER_PREFIX}", "--replicas=3"])
    wait_statefulset_ready(NAMESPACE, CLUSTER_PREFIX, timeout_sec=900)
    wait_until(
        backup_queue_restored,
        timeout_sec=180,
        interval_sec=5,
        description="app-backup queue restored with messages",
    )
    print("manual_backup_restore solver applied")


if __name__ == "__main__":
    main()
