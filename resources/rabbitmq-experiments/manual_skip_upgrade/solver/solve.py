#!/usr/bin/env python3
import os
import re
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from solver_utils import run, wait_statefulset_ready, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")
TO_VERSION = os.environ.get("BENCH_PARAM_TO_VERSION", "4.1")


def version_matches_target(version: str) -> bool:
    requested = TO_VERSION.strip()
    family_prefix = requested if requested.endswith(".") else f"{requested}."
    return version == requested or version.startswith(family_prefix)


def versions_match_target():
    for pod in (f"{CLUSTER_PREFIX}-0", f"{CLUSTER_PREFIX}-1", f"{CLUSTER_PREFIX}-2"):
        out = run(["kubectl", "-n", NAMESPACE, "exec", pod, "--", "rabbitmqctl", "status"])
        match = re.search(r"RabbitMQ\s*version\s*[:=]\s*([0-9]+\.[0-9]+\.[0-9]+)", out)
        if not match:
            match = re.search(r'"RabbitMQ"\s*,\s*"([0-9]+\.[0-9]+\.[0-9]+)"', out)
        if not match:
            return False
        if not version_matches_target(match.group(1)):
            return False
    return True


def main():
    target_image = f"rabbitmq:{TO_VERSION}-management"
    run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "set",
            "image",
            f"statefulset/{CLUSTER_PREFIX}",
            f"rabbitmq={target_image}",
        ]
    )
    wait_statefulset_ready(NAMESPACE, CLUSTER_PREFIX, timeout_sec=1200)
    wait_until(
        versions_match_target,
        timeout_sec=240,
        interval_sec=10,
        description=f"all rabbitmq pods to run {TO_VERSION}.x or exact {TO_VERSION}",
    )
    print("manual_skip_upgrade solver applied")


if __name__ == "__main__":
    main()
