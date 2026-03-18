#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[1] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import bench_ns, run  # noqa: E402


ROLE_MATRIX = (
    ("team_a", "a"),
    ("team_b", "b"),
    ("team_c", "c"),
    ("team_d", "d"),
)

RBAC_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "common" / "resource" / "rbac.yaml"
).read_text(encoding="utf-8")


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


def apply_manifest(text: str) -> None:
    proc = subprocess.run(["kubectl", "apply", "-f", "-"], input=text, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "kubectl apply failed")


def rbac_probe(tenants: list[dict[str, str]]) -> int:
    for tenant in tenants:
        proc = run(
            ["kubectl", "-n", tenant["namespace"], "get", "serviceaccount", tenant["service_account"]],
            check=False,
        )
        if proc.returncode != 0:
            print(f"serviceaccount/{tenant['service_account']} missing in namespace {tenant['namespace']}")
            return 1
    print(f"all {len(tenants)} tenant service accounts are present")
    return 0


def rbac_apply(tenants: list[dict[str, str]]) -> int:
    try:
        for tenant in tenants:
            rendered = (
                RBAC_TEMPLATE.replace("__NAMESPACE__", tenant["namespace"]).replace(
                    "__SERVICE_ACCOUNT__", tenant["service_account"]
                )
            )
            apply_manifest(rendered)
    except Exception as exc:
        print(str(exc))
        return 1
    print(f"applied RBAC for {len(tenants)} tenant namespaces")
    return 0


def job_baseline_probe(tenants: list[dict[str, str]]) -> int:
    for tenant in tenants:
        proc = run(["kubectl", "-n", tenant["namespace"], "get", "job", tenant["job_name"]], check=False)
        if proc.returncode == 0:
            print(f"job/{tenant['job_name']} already exists in namespace {tenant['namespace']}")
            return 1
    print(f"all {len(tenants)} tenant job baselines are clean")
    return 0


def job_baseline_apply(tenants: list[dict[str, str]]) -> int:
    for tenant in tenants:
        proc = run(
            [
                "kubectl",
                "-n",
                tenant["namespace"],
                "delete",
                "job",
                tenant["job_name"],
                "--ignore-not-found=true",
                "--wait=true",
            ],
            check=False,
        )
        if proc.returncode != 0:
            print(proc.stderr.strip() or proc.stdout.strip() or f"failed deleting job/{tenant['job_name']}")
            return 1
    print(f"cleared tenant jobs across {len(tenants)} namespaces")
    return 0


def cleanup(tenants: list[dict[str, str]]) -> int:
    for tenant in tenants:
        commands = [
            [
                "kubectl",
                "-n",
                tenant["namespace"],
                "delete",
                "job",
                tenant["job_name"],
                "--ignore-not-found=true",
                "--wait=true",
            ],
            [
                "kubectl",
                "-n",
                tenant["namespace"],
                "delete",
                "rolebinding",
                f"{tenant['service_account']}-role-binding",
                "--ignore-not-found=true",
            ],
            [
                "kubectl",
                "-n",
                tenant["namespace"],
                "delete",
                "role",
                f"{tenant['service_account']}-role",
                "--ignore-not-found=true",
            ],
            [
                "kubectl",
                "-n",
                tenant["namespace"],
                "delete",
                "serviceaccount",
                tenant["service_account"],
                "--ignore-not-found=true",
            ],
        ]
        for command in commands:
            proc = run(command, check=False)
            if proc.returncode != 0:
                print(proc.stderr.strip() or proc.stdout.strip() or f"cleanup failed for namespace {tenant['namespace']}")
                return 1
    print(f"cleaned up {len(tenants)} tenant namespaces")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--action",
        required=True,
        choices=["rbac-probe", "rbac-apply", "job-baseline-probe", "job-baseline-apply", "cleanup"],
    )
    parser.add_argument("--tenant-count", type=int, required=True)
    parser.add_argument("--service-account-prefix", required=True)
    parser.add_argument("--job-name-prefix", required=True)
    args = parser.parse_args()

    tenants = active_tenants(args.tenant_count, args.service_account_prefix, args.job_name_prefix)
    if args.action == "rbac-probe":
        return rbac_probe(tenants)
    if args.action == "rbac-apply":
        return rbac_apply(tenants)
    if args.action == "job-baseline-probe":
        return job_baseline_probe(tenants)
    if args.action == "job-baseline-apply":
        return job_baseline_apply(tenants)
    return cleanup(tenants)


if __name__ == "__main__":
    raise SystemExit(main())
