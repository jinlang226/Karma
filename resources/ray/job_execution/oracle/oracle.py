#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    configmap_value,
    configmap,
    deployment_ready_replicas,
    job_logs,
    job_succeeded,
    names_from_env,
    service_ports,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["cluster", "script", "job-runner", "result"])
    parser.add_argument("--expected-workers", type=int, required=True)
    parser.add_argument("--expected-output", required=True)
    args = parser.parse_args()

    ns = bench_namespace()
    names = names_from_env()

    if args.check == "cluster":
        ports = service_ports(ns, names.head)
        if 6379 not in ports:
            print(f"service/{names.head} does not expose port 6379")
            return 1
        if 10001 not in ports:
            print(f"service/{names.head} does not expose port 10001")
            return 1
        head_ready = deployment_ready_replicas(ns, names.head)
        if head_ready < 1:
            print(f"deployment/{names.head} ready replicas {head_ready}, expected at least 1")
            return 1
        worker_ready = deployment_ready_replicas(ns, names.worker)
        if worker_ready < args.expected_workers:
            print(
                f"deployment/{names.worker} ready replicas {worker_ready}, expected at least {args.expected_workers}"
            )
            return 1
        print("ray cluster baseline is healthy")
        return 0

    if args.check == "script":
        script = str((configmap(ns, names.job_script).get("data", {}) or {}).get("job.py") or "")
        if not script:
            print(f"configmap/{names.job_script} is missing key 'job.py'")
            return 1
        expected_address = f'ray.init(address="ray://{names.head}:10001")'
        required_snippets = [expected_address, "def ping():", 'return "pong"', "print(ray.get(ping.remote()))"]
        missing = [snippet for snippet in required_snippets if snippet not in script]
        if missing:
            print(f"configmap/{names.job_script} job.py is missing expected content: {missing!r}")
            return 1
        print(f"configmap/{names.job_script} retains the expected script content")
        return 0

    if args.check == "job-runner":
        if not job_succeeded(ns, names.job_runner):
            print(f"job/{names.job_runner} did not complete successfully")
            return 1
        logs = job_logs(ns, names.job_runner)
        lines = [line.strip() for line in logs.splitlines() if line.strip()]
        if not lines:
            print(f"job/{names.job_runner} produced no logs")
            return 1
        if lines[-1] != args.expected_output:
            print(f"job/{names.job_runner} last log line {lines[-1]!r}, expected {args.expected_output!r}")
            return 1
        print(f"job/{names.job_runner} completed with expected output {args.expected_output!r}")
        return 0

    result = configmap_value(ns, f"{names.cluster_prefix}-job-result", "result")
    if not result:
        print(f"configmap/{names.cluster_prefix}-job-result is missing key 'result'")
        return 1
    if result != args.expected_output:
        print(
            f"configmap/{names.cluster_prefix}-job-result result {result!r}, expected {args.expected_output!r}"
        )
        return 1
    print(f"configmap/{names.cluster_prefix}-job-result recorded {result!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
