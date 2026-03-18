#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    job,
    job_logs,
    job_mount_path,
    job_pvc_claim,
    job_succeeded,
    pvc_exists,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["storage", "job-spec", "job-status", "logs"])
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--pvc-name", required=True)
    parser.add_argument("--expected-service-account", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-data-mount", required=True)
    args = parser.parse_args()

    namespace = bench_namespace("spark-etl")

    if args.check == "storage":
        if not pvc_exists(namespace, args.pvc_name):
            print(f"pvc/{args.pvc_name} does not exist")
            return 1
        print(f"pvc/{args.pvc_name} exists")
        return 0

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
        mount_path = job_mount_path(namespace, args.job_name, "etl-data")
        if mount_path != args.expected_data_mount:
            print(
                f"job/{args.job_name} etl-data mountPath={mount_path!r}, "
                f"expected {args.expected_data_mount!r}"
            )
            return 1
        claim_name = job_pvc_claim(namespace, args.job_name, "etl-data")
        if claim_name != args.pvc_name:
            print(f"job/{args.job_name} etl-data claimName={claim_name!r}, expected {args.pvc_name!r}")
            return 1
        print(f"job/{args.job_name} spec matches the expected image, service account, and PVC wiring")
        return 0

    if args.check == "job-status":
        if not job_succeeded(namespace, args.job_name):
            print(f"job/{args.job_name} did not complete successfully")
            return 1
        print(f"job/{args.job_name} completed successfully")
        return 0

    logs = job_logs(namespace, args.job_name)
    for token in ("alice:30", "carol:15", "spark_etl_pipeline_verified"):
        if token not in logs:
            print(f"job/{args.job_name} logs do not contain required token {token!r}")
            return 1
    print(f"job/{args.job_name} logs confirm the ETL pipeline output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
