#!/usr/bin/env python3
import argparse
import json
import os
import subprocess


def read_configmap(name: str, namespace: str) -> dict:
    cmd = ["kubectl", "-n", str(namespace), "get", "configmap", name, "-o", "json"]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"failed to read {name}")
    return json.loads(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-phase", default="alpha")
    parser.add_argument("--expected-replicas", default="5")
    parser.add_argument("--namespace", default=os.environ.get("BENCH_NAMESPACE", "workflow-mock"))
    args = parser.parse_args()
    try:
        state = read_configmap("wf-state", args.namespace)
    except Exception as exc:  # noqa: BLE001
        print(str(exc))
        return 1

    data = state.get("data") or {}
    phase = (data.get("phase") or "").strip()
    replicas = (data.get("replicas") or "").strip()
    if phase != str(args.expected_phase):
        print(f"expected phase={args.expected_phase}, got {phase!r}")
        return 1
    if replicas != str(args.expected_replicas):
        print(f"expected replicas={args.expected_replicas}, got {replicas!r}")
        return 1

    print(f"stage_scale oracle passed namespace={args.namespace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
