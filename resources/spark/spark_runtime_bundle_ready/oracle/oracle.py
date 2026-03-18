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
    deployment_container_image,
    deployment_ready_replicas,
    deployment_service_account,
    job,
    job_logs,
    job_succeeded,
    secret_value,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["config", "secret", "monitor", "job-spec", "job-status", "logs"])
    parser.add_argument("--configmap-name", required=True)
    parser.add_argument("--secret-name", required=True)
    parser.add_argument("--deployment-name", required=True)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--expected-service-account", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-executor-memory", required=True)
    parser.add_argument("--expected-driver-memory", required=True)
    parser.add_argument("--expected-api-key", required=True)
    args = parser.parse_args()

    namespace = bench_namespace("spark-runtime")

    if args.check == "config":
        executor_memory = configmap_value(namespace, args.configmap_name, "spark.executor.memory")
        if executor_memory != args.expected_executor_memory:
            print(
                f"configmap/{args.configmap_name} spark.executor.memory={executor_memory!r}, "
                f"expected {args.expected_executor_memory!r}"
            )
            return 1
        driver_memory = configmap_value(namespace, args.configmap_name, "spark.driver.memory")
        if driver_memory != args.expected_driver_memory:
            print(
                f"configmap/{args.configmap_name} spark.driver.memory={driver_memory!r}, "
                f"expected {args.expected_driver_memory!r}"
            )
            return 1
        print(f"configmap/{args.configmap_name} carries the expected Spark runtime settings")
        return 0

    if args.check == "secret":
        api_key = secret_value(namespace, args.secret_name, "api-key")
        if api_key != args.expected_api_key:
            print(f"secret/{args.secret_name} api-key={api_key!r}, expected {args.expected_api_key!r}")
            return 1
        print(f"secret/{args.secret_name} carries the expected API key")
        return 0

    if args.check == "monitor":
        ready = deployment_ready_replicas(namespace, args.deployment_name)
        if ready != 1:
            print(f"deployment/{args.deployment_name} readyReplicas={ready}, expected 1")
            return 1
        service_account = deployment_service_account(namespace, args.deployment_name)
        if service_account != args.expected_service_account:
            print(
                f"deployment/{args.deployment_name} serviceAccountName={service_account!r}, "
                f"expected {args.expected_service_account!r}"
            )
            return 1
        image = deployment_container_image(namespace, args.deployment_name)
        if image != args.expected_image:
            print(
                f"deployment/{args.deployment_name} image={image!r}, expected {args.expected_image!r}"
            )
            return 1
        print(f"deployment/{args.deployment_name} is ready with the expected image and service account")
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
        print(f"job/{args.job_name} spec matches expected service account and image")
        return 0

    if args.check == "job-status":
        if not job_succeeded(namespace, args.job_name):
            print(f"job/{args.job_name} did not complete successfully")
            return 1
        print(f"job/{args.job_name} completed successfully")
        return 0

    logs = job_logs(namespace, args.job_name)
    required_tokens = [
        f"executor.memory={args.expected_executor_memory}",
        f"driver.memory={args.expected_driver_memory}",
        "Pi is roughly",
        "spark_runtime_bundle_verified",
    ]
    for token in required_tokens:
        if token not in logs:
            print(f"job/{args.job_name} logs do not contain required token {token!r}")
            return 1
    print(f"job/{args.job_name} logs confirm the runtime bundle and Spark execution path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
