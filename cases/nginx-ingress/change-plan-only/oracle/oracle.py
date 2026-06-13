#!/usr/bin/env python3
"""Oracle for nginx-ingress/change-plan-only.

Verifies the agent wrote a change/migration plan as a ConfigMap WITHOUT applying
any changes: the `change-plan` ConfigMap must exist with a non-empty `plan.md`
key. Whether the agent (wrongly) applied changes and reverted earlier stages is
caught separately by the workflow's regression sweep.
"""
from __future__ import annotations

import subprocess
import sys

NAMESPACE = "demo"
CONFIGMAP = "change-plan"
KEY = "plan.md"


def run(cmd):
    """Run a subprocess command and return the completed process."""
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main() -> int:
    """Check the change-plan ConfigMap exists with a non-empty plan."""
    proc = run([
        "kubectl", "-n", NAMESPACE, "get", "configmap", CONFIGMAP,
        "-o", f"jsonpath={{.data.{KEY}}}",
    ])
    if proc.returncode != 0:
        print(f"change-plan verification failed: ConfigMap "
              f"'{CONFIGMAP}' not found in namespace '{NAMESPACE}': "
              f"{proc.stderr.strip()}", file=sys.stderr)
        return 1
    plan = (proc.stdout or "").strip()
    if len(plan) < 20:
        print(f"change-plan verification failed: ConfigMap "
              f"'{CONFIGMAP}' key '{KEY}' is missing or too short to be a real "
              f"plan (got {len(plan)} chars)", file=sys.stderr)
        return 1
    print(f"change-plan prepared: ConfigMap '{CONFIGMAP}' has a "
          f"{len(plan)}-char '{KEY}' plan (no changes applied).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
