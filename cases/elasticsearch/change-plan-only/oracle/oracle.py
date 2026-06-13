#!/usr/bin/env python3
"""Oracle for elasticsearch/change-plan-only.

Verifies the agent wrote a change/migration plan as a ConfigMap WITHOUT applying
any changes: the `change-plan` ConfigMap must exist with a non-empty `plan.md`
key. Whether the agent (wrongly) mutated the cluster is caught separately by the
workflow's regression sweep, which re-runs earlier stages' oracles against the
final cluster state.
"""
from __future__ import annotations

import subprocess
import sys

NAMESPACE = "elasticsearch"
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
              f"migration plan (got {len(plan)} chars)", file=sys.stderr)
        return 1
    print(f"change-plan-only passed: ConfigMap '{CONFIGMAP}' has a "
          f"{len(plan)}-char '{KEY}' plan (no cluster changes applied).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
