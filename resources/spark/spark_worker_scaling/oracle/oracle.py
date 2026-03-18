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
    deployment_ready_replicas,
    deployment_service_account,
    deployment_spec_replicas,
    service_ports,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["service", "master", "worker-spec", "worker-ready"])
    parser.add_argument("--cluster-prefix", required=True)
    parser.add_argument("--expected-service-account", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-workers", required=True, type=int)
    parser.add_argument("--expected-worker-memory", required=True)
    args = parser.parse_args()

    namespace = bench_namespace("spark-scaling")
    master_name = f"{args.cluster_prefix}-master"
    worker_name = f"{args.cluster_prefix}-worker"

    if args.check == "service":
        ports = service_ports(namespace, master_name)
        missing = {7077, 8080} - ports
        if missing:
            print(f"service/{master_name} is missing ports: {sorted(missing)}")
            return 1
        print(f"service/{master_name} exposes Spark and Web UI ports")
        return 0

    if args.check == "master":
        ready = deployment_ready_replicas(namespace, master_name)
        if ready != 1:
            print(f"deployment/{master_name} readyReplicas={ready}, expected 1")
            return 1
        service_account = deployment_service_account(namespace, master_name)
        if service_account != args.expected_service_account:
            print(
                f"deployment/{master_name} serviceAccountName={service_account!r}, "
                f"expected {args.expected_service_account!r}"
            )
            return 1
        image = deployment_container_image(namespace, master_name)
        if image != args.expected_image:
            print(f"deployment/{master_name} image={image!r}, expected {args.expected_image!r}")
            return 1
        print(f"deployment/{master_name} is ready with the expected service account and image")
        return 0

    if args.check == "worker-spec":
        spec_replicas = deployment_spec_replicas(namespace, worker_name)
        if spec_replicas != args.expected_workers:
            print(f"deployment/{worker_name} spec.replicas={spec_replicas}, expected {args.expected_workers}")
            return 1
        service_account = deployment_service_account(namespace, worker_name)
        if service_account != args.expected_service_account:
            print(
                f"deployment/{worker_name} serviceAccountName={service_account!r}, "
                f"expected {args.expected_service_account!r}"
            )
            return 1
        image = deployment_container_image(namespace, worker_name)
        if image != args.expected_image:
            print(f"deployment/{worker_name} image={image!r}, expected {args.expected_image!r}")
            return 1
        memory = deployment_env(namespace, worker_name, "SPARK_WORKER_MEMORY")
        if memory != args.expected_worker_memory:
            print(
                f"deployment/{worker_name} SPARK_WORKER_MEMORY={memory!r}, "
                f"expected {args.expected_worker_memory!r}"
            )
            return 1
        print(f"deployment/{worker_name} spec matches expected replicas, image, and worker memory")
        return 0

    ready = deployment_ready_replicas(namespace, worker_name)
    if ready != args.expected_workers:
        print(f"deployment/{worker_name} readyReplicas={ready}, expected {args.expected_workers}")
        return 1
    print(f"deployment/{worker_name} has {ready} ready workers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
