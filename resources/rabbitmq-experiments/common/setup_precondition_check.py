#!/usr/bin/env python3
import argparse
import os
import json
import subprocess
import sys


BAD_WAITING_REASONS = {
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerConfigError",
    "CreateContainerError",
    "RunContainerError",
}


def _run_kubectl(namespace):
    cmd = ["kubectl", "-n", namespace, "get", "pods", "-o", "json"]
    out = subprocess.check_output(cmd, text=True)
    return json.loads(out)


def _is_job_pod(pod):
    owners = pod.get("metadata", {}).get("ownerReferences") or []
    for owner in owners:
        if (owner or {}).get("kind") == "Job":
            return True
    return False


def _is_ready(pod):
    status = pod.get("status") or {}
    for cond in status.get("conditions") or []:
        if (cond or {}).get("type") == "Ready":
            return (cond or {}).get("status") == "True"
    return False


def _bad_wait_reason(pod):
    status = pod.get("status") or {}
    for container in status.get("containerStatuses") or []:
        state = (container or {}).get("state") or {}
        waiting = state.get("waiting") or {}
        reason = waiting.get("reason")
        if reason in BAD_WAITING_REASONS:
            return reason
    return None


def main():
    parser = argparse.ArgumentParser(description="Validate setup preconditions for RabbitMQ experiments.")
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--min-ready", type=int, default=1)
    args = parser.parse_args()

    try:
        payload = _run_kubectl(args.namespace)
    except Exception as exc:
        print(f"precondition-check: failed to query pods: {exc}", file=sys.stderr)
        return 1

    items = payload.get("items") or []
    if not items:
        print("precondition-check: no pods found", file=sys.stderr)
        return 1

    ready_non_job = 0
    failures = []
    for pod in items:
        meta = pod.get("metadata") or {}
        status = pod.get("status") or {}
        name = meta.get("name") or "<unknown>"
        phase = status.get("phase") or "Unknown"

        if _is_job_pod(pod) and phase == "Succeeded":
            continue
        if meta.get("deletionTimestamp"):
            failures.append(f"{name}: deleting")
            continue
        if phase != "Running":
            failures.append(f"{name}: phase={phase}")
            continue
        bad_reason = _bad_wait_reason(pod)
        if bad_reason:
            failures.append(f"{name}: waiting={bad_reason}")
            continue
        if not _is_ready(pod):
            failures.append(f"{name}: not-ready")
            continue
        ready_non_job += 1

    if ready_non_job < max(1, args.min_ready):
        failures.append(f"ready_non_job={ready_non_job} < min_ready={max(1, args.min_ready)}")

    if failures:
        print("precondition-check: not stable", file=sys.stderr)
        for item in failures:
            print(f" - {item}", file=sys.stderr)
        return 1

    print(f"precondition-check: ok ready_non_job={ready_non_job}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
