#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import job, job_logs, job_succeeded  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["job-spec", "job-status", "logs"])
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--expected-service-account", required=True)
    parser.add_argument("--expected-image", required=True)
    args = parser.parse_args()

    if args.check == "job-spec":
        payload = job(args.namespace, args.job_name)
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
        if not job_succeeded(args.namespace, args.job_name):
            print(f"job/{args.job_name} did not complete successfully in namespace {args.namespace}")
            return 1
        print(f"job/{args.job_name} completed successfully in namespace {args.namespace}")
        return 0

    logs = job_logs(args.namespace, args.job_name)
    match = re.search(r"Pi is roughly ([0-9.]+)", logs)
    if not match:
        print(f"job/{args.job_name} logs do not contain a Pi result in namespace {args.namespace}")
        return 1
    try:
        pi_value = float(match.group(1))
    except ValueError:
        print(f"job/{args.job_name} emitted an unparsable Pi result in namespace {args.namespace}")
        return 1
    if not 3.0 <= pi_value <= 3.3:
        print(f"job/{args.job_name} Pi value {pi_value} is outside expected range in namespace {args.namespace}")
        return 1
    print(f"job/{args.job_name} logs report Pi value {pi_value} in namespace {args.namespace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
