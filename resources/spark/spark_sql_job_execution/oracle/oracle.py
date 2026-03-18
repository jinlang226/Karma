#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import bench_namespace, job, job_logs, job_succeeded  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["job-spec", "job-status", "logs"])
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--expected-service-account", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-summary", required=True)
    args = parser.parse_args()

    namespace = bench_namespace("spark-sql")

    if args.check == "job-spec":
        payload = job(namespace, args.job_name)
        spec = payload.get("spec", {}).get("template", {}).get("spec", {}) or {}
        service_account = str(spec.get("serviceAccountName") or "")
        if service_account != args.expected_service_account:
            print(
                f"job/{args.job_name} serviceAccountName={service_account!r}, "
                f"expected {args.expected_service_account!r}"
            )
            return 1
        containers = spec.get("containers", []) or []
        if not containers:
            print(f"job/{args.job_name} has no containers")
            return 1
        image = str(containers[0].get("image") or "")
        if image != args.expected_image:
            print(f"job/{args.job_name} image={image!r}, expected {args.expected_image!r}")
            return 1
        print(f"job/{args.job_name} spec matches expected service account and image")
        return 0

    if args.check == "job-status":
        if not job_succeeded(namespace, args.job_name):
            print(f"job/{args.job_name} did not complete successfully")
            return 1
        print(f"job/{args.job_name} completed successfully")
        return 0

    logs = job_logs(namespace, args.job_name)
    for token in (args.expected_summary, "spark_sql_query_verified"):
        if token not in logs:
            print(f"job/{args.job_name} logs do not contain required token {token!r}")
            return 1
    print(f"job/{args.job_name} logs confirm the SQL query result")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
