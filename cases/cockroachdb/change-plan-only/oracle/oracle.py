#!/usr/bin/env python3
"""Oracle for cockroachdb/change-plan-only.

Verifies the agent prepared a written change/migration plan as a ConfigMap
WITHOUT applying any of it: the `change-plan` ConfigMap must exist with a
non-empty `plan.md` key. Whether the agent (wrongly) mutated cluster state is
caught separately by the workflow's regression sweep, which re-runs earlier
stages' oracles against the final cluster state.
"""
from __future__ import annotations

import subprocess
import sys

NAMESPACE = "cockroachdb"
CONFIGMAP = "change-plan"
KEY = "plan.md"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main() -> int:
    """Check the change-plan ConfigMap exists with a non-empty plan document."""
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
              f"plan document (got {len(plan)} chars)", file=sys.stderr)
        return 1
    print(f"change-plan prepared: ConfigMap '{CONFIGMAP}' has a "
          f"{len(plan)}-char '{KEY}' document (not applied).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
