#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-phase", default="alpha")
    parser.add_argument("--namespace", default=os.environ.get("BENCH_NAMESPACE", "workflow-mock"))
    args = parser.parse_args()
    cmd = [
        "kubectl",
        "-n",
        str(args.namespace),
        "get",
        "configmap",
        "wf-state",
        "-o",
        "json",
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        print(proc.stderr.strip() or proc.stdout.strip() or "failed to read wf-state")
        return 1
    payload = json.loads(proc.stdout)
    phase = ((payload.get("data") or {}).get("phase") or "").strip()
    if phase != str(args.expected_phase):
        print(f"expected phase={args.expected_phase}, got phase={phase!r}")
        return 1
    print(f"stage_seed oracle passed namespace={args.namespace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
