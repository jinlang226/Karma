#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configmap", default="demo-config")
    parser.add_argument("--key", default="value")
    parser.add_argument("--expected-value", required=True)
    parser.add_argument("--namespace", default=os.environ.get("BENCH_NAMESPACE", "default"))
    args = parser.parse_args()

    cmd = [
        "kubectl",
        "-n",
        str(args.namespace),
        "get",
        "configmap",
        str(args.configmap),
        "-o",
        "json",
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        print(proc.stderr.strip() or proc.stdout.strip() or f"failed to read configmap {args.configmap}")
        return 1

    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        print(f"failed to parse kubectl output: {exc}")
        return 1

    data = payload.get("data") or {}
    actual = str(data.get(str(args.key), ""))
    expected = str(args.expected_value)
    if actual != expected:
        print(
            f"oracle mismatch namespace={args.namespace} configmap={args.configmap} "
            f"key={args.key} expected={expected!r} actual={actual!r}"
        )
        return 1

    print(
        f"oracle ok namespace={args.namespace} configmap={args.configmap} "
        f"key={args.key} value={expected!r}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
