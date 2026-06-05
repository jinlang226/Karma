#!/usr/bin/env python3
"""Oracle for spark/spark_etl_skew_oom.

Verifies the data-skew OOM in Stage 3 was diagnosed and remediated:
  - the etl-job Job completed successfully, and
  - at least one valid fix is in place: executor memory was raised above the
    failing 256m baseline, the worker deployment was scaled out / given more
    memory, or Adaptive Query Execution (skew handling) was enabled on the job.
"""
from __future__ import annotations

import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    deployment_env,
    deployment_spec_replicas,
    job,
    job_logs,
    job_succeeded,
)

NAMESPACE = bench_namespace("spark-etl")
JOB_NAME = "etl-job"
WORKER_DEPLOYMENT = "spark-worker"


def fail(message: str) -> int:
    print(f"spark_etl_skew_oom oracle failed: {message}")
    return 1


def job_command_text() -> str:
    payload = job(NAMESPACE, JOB_NAME)
    containers = payload.get("spec", {}).get("template", {}).get("spec", {}).get("containers", []) or []
    if not containers:
        return ""
    return " ".join(str(part) for part in (containers[0].get("command") or []))


def fix_applied() -> bool:
    """True if any of the documented remediations is detectable in cluster state."""
    command = job_command_text().lower()

    # Option B: executor memory raised above the failing 256m baseline.
    if "spark.executor.memory=256m" not in command and "--executor-memory 256m" not in command:
        if "executor.memory" in command or "--executor-memory" in command:
            return True

    # Option C: Adaptive Query Execution / skew join enabled on the job.
    if "spark.sql.adaptive.enabled=true" in command or "skewjoin.enabled=true" in command:
        return True

    # Option A: workers scaled out and/or given more memory.
    try:
        if deployment_spec_replicas(NAMESPACE, WORKER_DEPLOYMENT) > 1:
            return True
        worker_memory = deployment_env(NAMESPACE, WORKER_DEPLOYMENT, "SPARK_WORKER_MEMORY")
        if worker_memory and worker_memory not in ("1G", "1g"):
            return True
    except Exception:
        pass
    return False


def main() -> int:
    if not job_succeeded(NAMESPACE, JOB_NAME):
        return fail(f"job/{JOB_NAME} did not complete successfully")

    logs = job_logs(NAMESPACE, JOB_NAME)
    if "spark_etl_pipeline_verified" not in logs:
        return fail(f"job/{JOB_NAME} logs do not confirm pipeline completion")

    if not fix_applied():
        return fail(
            "no remediation detected (expected raised executor memory, scaled/upsized "
            "workers, or enabled AQE skew handling)"
        )

    print("spark_etl_skew_oom verified: Stage 3 OOM remediated and ETL job completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
