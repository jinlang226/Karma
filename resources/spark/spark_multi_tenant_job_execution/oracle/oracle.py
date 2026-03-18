#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import bench_ns, job, job_logs, job_succeeded  # noqa: E402


ROLE_MATRIX = (
    ("team_a", "a"),
    ("team_b", "b"),
    ("team_c", "c"),
    ("team_d", "d"),
)


def active_tenants(tenant_count: int, service_account_prefix: str, job_name_prefix: str) -> list[dict[str, str]]:
    tenants: list[dict[str, str]] = []
    for role, suffix in ROLE_MATRIX[:tenant_count]:
        namespace = bench_ns(role)
        if not namespace:
            raise RuntimeError(f"missing namespace binding for role {role}")
        tenants.append(
            {
                "role": role,
                "namespace": namespace,
                "suffix": suffix,
                "service_account": f"{service_account_prefix}-{suffix}",
                "job_name": f"{job_name_prefix}-{suffix}",
            }
        )
    return tenants


def check_job_spec(tenant: dict[str, str], expected_image: str) -> tuple[bool, str]:
    payload = job(tenant["namespace"], tenant["job_name"])
    spec = payload.get("spec", {}).get("template", {}).get("spec", {}) or {}
    service_account = str(spec.get("serviceAccountName") or "")
    if service_account != tenant["service_account"]:
        return (
            False,
            f"job/{tenant['job_name']} serviceAccountName={service_account!r}, "
            f"expected {tenant['service_account']!r} in namespace {tenant['namespace']}",
        )
    containers = spec.get("containers", []) or []
    if not containers:
        return False, f"job/{tenant['job_name']} has no containers in namespace {tenant['namespace']}"
    image = str(containers[0].get("image") or "")
    if image != expected_image:
        return (
            False,
            f"job/{tenant['job_name']} image={image!r}, expected {expected_image!r} in namespace {tenant['namespace']}",
        )
    return True, f"job/{tenant['job_name']} spec matches expected image and service account in {tenant['namespace']}"


def check_job_status(tenant: dict[str, str]) -> tuple[bool, str]:
    if not job_succeeded(tenant["namespace"], tenant["job_name"]):
        return False, f"job/{tenant['job_name']} did not complete successfully in namespace {tenant['namespace']}"
    return True, f"job/{tenant['job_name']} completed successfully in namespace {tenant['namespace']}"


def check_logs(tenant: dict[str, str]) -> tuple[bool, str]:
    logs = job_logs(tenant["namespace"], tenant["job_name"])
    match = re.search(r"Pi is roughly ([0-9.]+)", logs)
    if not match:
        return False, f"job/{tenant['job_name']} logs do not contain a Pi result in namespace {tenant['namespace']}"
    try:
        pi_value = float(match.group(1))
    except ValueError:
        return False, f"job/{tenant['job_name']} emitted an unparsable Pi result in namespace {tenant['namespace']}"
    if not 3.0 <= pi_value <= 3.3:
        return (
            False,
            f"job/{tenant['job_name']} Pi value {pi_value} is outside expected range in namespace {tenant['namespace']}",
        )
    return True, f"job/{tenant['job_name']} logs report Pi value {pi_value} in namespace {tenant['namespace']}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["job-spec", "job-status", "logs"])
    parser.add_argument("--tenant-count", required=True, type=int)
    parser.add_argument("--service-account-prefix", required=True)
    parser.add_argument("--job-name-prefix", required=True)
    parser.add_argument("--expected-image", required=True)
    args = parser.parse_args()

    tenants = active_tenants(args.tenant_count, args.service_account_prefix, args.job_name_prefix)
    checker = {
        "job-spec": lambda tenant: check_job_spec(tenant, args.expected_image),
        "job-status": check_job_status,
        "logs": check_logs,
    }[args.check]

    for tenant in tenants:
        ok, message = checker(tenant)
        print(message)
        if not ok:
            return 1
    print(f"{args.check} passed across {len(tenants)} tenant namespaces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
