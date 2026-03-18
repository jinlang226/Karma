#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    deployment_container_image,
    deployment_env,
    deployment_mount_path,
    deployment_pvc_claim,
    deployment_ready_replicas,
    deployment_service_account,
    pvc_exists,
    service_ports,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["storage", "service", "deployment", "wiring"])
    parser.add_argument("--deployment-name", required=True)
    parser.add_argument("--service-name", required=True)
    parser.add_argument("--pvc-name", required=True)
    parser.add_argument("--expected-service-account", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-log-dir", required=True)
    parser.add_argument("--expected-service-port", required=True, type=int)
    parser.add_argument("--expected-replicas", required=True, type=int)
    args = parser.parse_args()

    namespace = bench_namespace("spark-history")

    if args.check == "storage":
        if not pvc_exists(namespace, args.pvc_name):
            print(f"pvc/{args.pvc_name} does not exist")
            return 1
        print(f"pvc/{args.pvc_name} exists")
        return 0

    if args.check == "service":
        ports = service_ports(namespace, args.service_name)
        if args.expected_service_port not in ports:
            print(f"service/{args.service_name} does not expose port {args.expected_service_port}")
            return 1
        print(f"service/{args.service_name} exposes port {args.expected_service_port}")
        return 0

    if args.check == "deployment":
        ready = deployment_ready_replicas(namespace, args.deployment_name)
        if ready != args.expected_replicas:
            print(
                f"deployment/{args.deployment_name} readyReplicas={ready}, expected {args.expected_replicas}"
            )
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
        print(
            f"deployment/{args.deployment_name} is ready with {args.expected_replicas} replica(s), "
            "the expected service account, and image"
        )
        return 0

    env_value = deployment_env(namespace, args.deployment_name, "SPARK_HISTORY_OPTS")
    if args.expected_log_dir not in env_value:
        print(
            f"deployment/{args.deployment_name} SPARK_HISTORY_OPTS={env_value!r}, "
            f"expected log dir {args.expected_log_dir!r}"
        )
        return 1
    mount_path = deployment_mount_path(namespace, args.deployment_name, "spark-logs")
    if mount_path != args.expected_log_dir:
        print(
            f"deployment/{args.deployment_name} spark-logs mountPath={mount_path!r}, "
            f"expected {args.expected_log_dir!r}"
        )
        return 1
    pvc_name = deployment_pvc_claim(namespace, args.deployment_name, "spark-logs")
    if pvc_name != args.pvc_name:
        print(
            f"deployment/{args.deployment_name} spark-logs claimName={pvc_name!r}, "
            f"expected {args.pvc_name!r}"
        )
        return 1
    print(f"deployment/{args.deployment_name} is wired to the expected log directory and PVC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
