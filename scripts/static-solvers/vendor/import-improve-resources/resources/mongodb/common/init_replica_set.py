#!/usr/bin/env python3
"""Initialize an unauthenticated MongoDB replica set from benchmark parameters."""

from __future__ import annotations

import json
import os
import subprocess
import time


def env_first(*names: str, default: str | None = None) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    if default is not None:
        return default
    raise RuntimeError(f"missing required environment variable: {' or '.join(names)}")


def kubectl(namespace: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", "-n", namespace, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def mongosh(namespace: str, pod: str, script: str) -> subprocess.CompletedProcess[str]:
    return kubectl(
        namespace,
        "exec",
        pod,
        "--",
        "mongosh",
        "--quiet",
        "--eval",
        script,
        timeout=60,
    )


def main() -> None:
    namespace = env_first("BENCH_NAMESPACE")
    cluster = env_first(
        "BENCH_PARAM_CLUSTER_PREFIX",
        "BENCH_PARAM_DATA_CLUSTER_PREFIX",
        default="mongodb-replica",
    )
    service = env_first(
        "BENCH_PARAM_HEADLESS_SERVICE_NAME",
        "BENCH_PARAM_SERVICE_NAME",
        "BENCH_PARAM_DATA_SERVICE_NAME",
        default=f"{cluster}-svc",
    )
    replica_set = env_first("BENCH_PARAM_REPLICA_SET_NAME", default=cluster)
    replicas = int(
        env_first(
            "BENCH_PARAM_EXPECTED_REPLICAS",
            "BENCH_PARAM_START_REPLICAS",
            "BENCH_PARAM_DATA_REPLICAS",
            default="3",
        )
    )
    pod = f"{cluster}-0"

    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        running = kubectl(
            namespace,
            "get",
            "statefulset",
            cluster,
            "-o",
            "jsonpath={.status.readyReplicas}",
        )
        if running.returncode == 0 and (running.stdout.strip() or "0") == str(replicas):
            break
        time.sleep(5)
    else:
        raise RuntimeError(f"{cluster} did not reach {replicas} ready replicas")

    hello = mongosh(namespace, pod, "JSON.stringify(db.hello())")
    if hello.returncode == 0:
        try:
            if json.loads(hello.stdout.strip()).get("setName") == replica_set:
                print("MongoDB replica set already initialized")
                return
        except json.JSONDecodeError:
            pass

    members = [
        {
            "_id": index,
            "host": (
                f"{cluster}-{index}.{service}.{namespace}.svc.cluster.local:27017"
            ),
        }
        for index in range(replicas)
    ]
    config = json.dumps({"_id": replica_set, "members": members}, separators=(",", ":"))
    initiated = mongosh(
        namespace,
        pod,
        f"try {{ rs.initiate({config}) }} catch (e) {{ "
        "if (e.codeName !== 'AlreadyInitialized') throw e; }",
    )
    if initiated.returncode != 0:
        raise RuntimeError(initiated.stderr or initiated.stdout)

    while time.monotonic() < deadline:
        primary = mongosh(namespace, pod, "db.hello().isWritablePrimary")
        if primary.returncode == 0 and primary.stdout.strip() == "true":
            print("MongoDB replica set initialized")
            return
        time.sleep(3)
    raise RuntimeError("MongoDB replica set did not elect a primary")


if __name__ == "__main__":
    main()
