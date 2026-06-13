#!/usr/bin/env python3
"""Oracle for rabbitmq/change-plan-only.

Verifies the agent produced a change/migration plan as a ConfigMap WITHOUT
applying any of it: the `change-plan` ConfigMap must exist with a non-empty
`plan.md` key. Whether the agent (wrongly) mutated the cluster is caught
separately by the workflow's regression sweep, which re-runs earlier stages'
oracles against the final cluster state.
"""
from __future__ import annotations

import os
import subprocess
import sys

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CONFIGMAP = "change-plan"
KEY = "plan.md"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main() -> int:
    """Check the change-plan ConfigMap exists with a non-empty plan."""
    proc = run([
        "kubectl", "-n", NAMESPACE, "get", "configmap", CONFIGMAP,
        "-o", f"jsonpath={{.data.{KEY}}}",
    ])
    if proc.returncode != 0:
        print(f"change-plan-only verification failed: ConfigMap "
              f"'{CONFIGMAP}' not found in namespace '{NAMESPACE}': "
              f"{proc.stderr.strip()}", file=sys.stderr)
        return 1
    plan = (proc.stdout or "").strip()
    if len(plan) < 20:
        print(f"change-plan-only verification failed: ConfigMap "
              f"'{CONFIGMAP}' key '{KEY}' is missing or too short to be a real "
              f"change plan (got {len(plan)} chars)", file=sys.stderr)
        return 1
    print(f"change-plan-only prepared: ConfigMap '{CONFIGMAP}' has a "
          f"{len(plan)}-char '{KEY}' plan (not applied).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
