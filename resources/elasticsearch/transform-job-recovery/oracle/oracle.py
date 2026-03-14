#!/usr/bin/env python3
import json
import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
CURL_POD = os.environ.get("BENCH_PARAM_CURL_POD_NAME", "curl-test")
TRANSFORM_ID = os.environ.get("BENCH_PARAM_TRANSFORM_ID", "events-by-service")
CHECKPOINT_CM = os.environ.get("BENCH_PARAM_CHECKPOINT_CONFIGMAP", "transform-checkpoint")
DEST_INDEX = os.environ.get("BENCH_PARAM_DEST_INDEX_NAME", "app-events-rollup")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def curl_json(path, errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            CURL_POD,
            "--",
            "curl",
            "-s",
            "-S",
            "--max-time",
            "5",
            f"http://{SERVICE}:9200{path}",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to query {path}: {detail}")
        return None
    output = result.stdout.strip()
    if not output:
        errors.append(f"Empty response for {path}")
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        errors.append(f"Failed to parse JSON from {path}")
        return None


def get_checkpoint_before(errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "configmap",
            CHECKPOINT_CM,
            "-o",
            "jsonpath={.data.checkpoint_before}",
        ]
    )
    if result.returncode != 0:
        errors.append("Unable to read checkpoint_before from configmap")
        return None
    value = (result.stdout or "").strip()
    try:
        return int(value)
    except (TypeError, ValueError):
        errors.append(f"Invalid checkpoint_before value: {value!r}")
        return None


def get_transform(errors):
    stats = curl_json(f"/_transform/{TRANSFORM_ID}/_stats", errors)
    if not isinstance(stats, dict):
        return None
    transforms = stats.get("transforms") or []
    if not transforms:
        errors.append("Transform stats missing")
        return None
    return transforms[0]


def extract_checkpoint(transform):
    checkpoint = (
        transform.get("checkpointing", {})
        .get("last", {})
        .get("checkpoint")
    )
    if checkpoint is None:
        checkpoint = (
            transform.get("stats", {})
            .get("checkpointing", {})
            .get("last", {})
            .get("checkpoint")
        )
    return checkpoint


def main():
    errors = []

    checkpoint_before = get_checkpoint_before(errors)
    transform = get_transform(errors)
    if transform:
        state = transform.get("state") or transform.get("stats", {}).get("state")
        if state != "started":
            errors.append(f"Transform state expected started, got {state}")

        checkpoint_now = extract_checkpoint(transform)
        if checkpoint_before is not None:
            if checkpoint_now is None:
                errors.append("Unable to read current checkpoint")
            elif checkpoint_now <= checkpoint_before:
                errors.append(
                    f"Checkpoint did not advance (before={checkpoint_before}, now={checkpoint_now})"
                )

    count = curl_json(f"/{DEST_INDEX}/_count", errors)
    if isinstance(count, dict):
        if count.get("count", 0) <= 0:
            errors.append("Destination index has no documents")

    if errors:
        print("Transform recovery verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("Transform recovery verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
