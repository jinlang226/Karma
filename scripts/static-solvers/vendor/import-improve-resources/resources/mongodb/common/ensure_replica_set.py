#!/usr/bin/env python3
"""Idempotently prepare the shared authenticated MongoDB replica-set fixture."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time


NAMESPACE = os.environ["BENCH_NAMESPACE"]
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongodb-replica")
HEADLESS_SERVICE = os.environ.get(
    "BENCH_PARAM_HEADLESS_SERVICE_NAME", "mongodb-replica-svc"
)
REPLICA_SET = os.environ.get("BENCH_PARAM_REPLICA_SET_NAME", "mongodb-replica")
ADMIN_SECRET = os.environ.get(
    "BENCH_PARAM_ADMIN_SECRET_NAME", "admin-user-password"
)
ADMIN_USER = os.environ.get("BENCH_PARAM_ADMIN_USERNAME", "admin-user")
EXPECTED_REPLICAS = int(os.environ.get("MONGODB_FIXTURE_REPLICAS", "3"))


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"command failed: {' '.join(command)}")
    return result


def kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["kubectl", "-n", NAMESPACE, *args], check=check)


def secret_password() -> str:
    result = kubectl(
        "get",
        "secret",
        ADMIN_SECRET,
        "-o",
        "jsonpath={.data.password}",
    )
    return base64.b64decode(result.stdout.strip()).decode()


def mongo(pod: str, expression: str, *, authenticated: bool) -> subprocess.CompletedProcess[str]:
    command = ["exec", pod, "--", "mongosh", "--quiet"]
    if authenticated:
        password = secret_password()
        command.append(
            f"mongodb://{ADMIN_USER}:{password}@localhost:27017/admin?directConnection=true"
        )
    command.extend(["--eval", expression])
    return kubectl(*command, check=False)


def wait_for_pods() -> None:
    deadline = time.monotonic() + 600
    for ordinal in range(EXPECTED_REPLICAS):
        pod = f"{CLUSTER_PREFIX}-{ordinal}"
        while time.monotonic() < deadline:
            result = kubectl(
                "get", "pod", pod, "-o", "jsonpath={.status.phase}", check=False
            )
            if result.returncode == 0 and result.stdout.strip() == "Running":
                break
            time.sleep(3)
        else:
            raise RuntimeError(f"{pod} did not reach Running")


def replica_status(authenticated: bool) -> dict | None:
    for ordinal in range(EXPECTED_REPLICAS):
        pod = f"{CLUSTER_PREFIX}-{ordinal}"
        result = mongo(pod, "JSON.stringify(rs.status())", authenticated=authenticated)
        if result.returncode != 0:
            continue
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            continue
    return None


def initiate_if_needed() -> None:
    if replica_status(authenticated=False) is not None:
        return
    hello = mongo(
        f"{CLUSTER_PREFIX}-0",
        "db.hello().setName || ''",
        authenticated=False,
    )
    if hello.returncode == 0 and hello.stdout.strip() == REPLICA_SET:
        return
    members = ",".join(
        (
            f'{{_id:{ordinal},host:"{CLUSTER_PREFIX}-{ordinal}.'
            f'{HEADLESS_SERVICE}.{NAMESPACE}.svc.cluster.local:27017"}}'
        )
        for ordinal in range(EXPECTED_REPLICAS)
    )
    result = mongo(
        f"{CLUSTER_PREFIX}-0",
        f'rs.initiate({{_id:"{REPLICA_SET}",members:[{members}]}})',
        authenticated=False,
    )
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode != 0 and "already initialized" not in combined:
        raise RuntimeError(combined.strip() or "rs.initiate failed")


def wait_for_primary(authenticated: bool) -> str:
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        for ordinal in range(EXPECTED_REPLICAS):
            pod = f"{CLUSTER_PREFIX}-{ordinal}"
            result = mongo(
                pod, "db.hello().isWritablePrimary", authenticated=authenticated
            )
            if result.returncode == 0 and result.stdout.strip() == "true":
                return pod
        time.sleep(3)
    raise RuntimeError("replica set did not elect a primary")


def ensure_admin() -> None:
    authenticated_status = replica_status(authenticated=True)
    if authenticated_status:
        return

    primary = wait_for_primary(authenticated=False)
    password = secret_password().replace("\\", "\\\\").replace('"', '\\"')
    expression = (
        'try { db.getSiblingDB("admin").createUser({'
        f'user:"{ADMIN_USER}",pwd:"{password}",roles:[{{role:"root",db:"admin"}}]'
        '}) } catch(e) { if (!String(e).includes("already exists")) throw e }'
    )
    result = mongo(primary, expression, authenticated=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    wait_for_primary(authenticated=True)


def verify() -> None:
    status = replica_status(authenticated=True)
    if not status:
        raise RuntimeError("authenticated rs.status() failed")
    members = status.get("members") or []
    primary = sum(member.get("stateStr") == "PRIMARY" for member in members)
    secondary = sum(member.get("stateStr") == "SECONDARY" for member in members)
    if len(members) != EXPECTED_REPLICAS or primary != 1 or secondary != EXPECTED_REPLICAS - 1:
        raise RuntimeError(
            f"unexpected topology: members={len(members)} primary={primary} secondary={secondary}"
        )


def main() -> int:
    try:
        wait_for_pods()
        initiate_if_needed()
        ensure_admin()
        verify()
    except Exception as exc:
        print(f"MongoDB fixture preparation failed: {exc}", file=sys.stderr)
        return 1
    print("MongoDB replica-set fixture ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
