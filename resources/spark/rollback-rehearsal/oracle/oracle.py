#!/usr/bin/env python3
"""Oracle for spark/rollback-rehearsal.

Verifies the agent prepared the rollback script as a ConfigMap WITHOUT executing
it: the `rollback-rehearsal` ConfigMap must exist with a non-empty `rollback.sh`
key. Whether the agent (wrongly) ran the rollback and reverted earlier stages is
caught separately by the workflow's regression sweep, which re-runs those stages'
oracles against the final cluster state.
"""
from __future__ import annotations

import subprocess
import sys

NAMESPACE = "spark-pi"
CONFIGMAP = "rollback-rehearsal"
KEY = "rollback.sh"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main() -> int:
    """Check the rollback-rehearsal ConfigMap exists with a non-empty script."""
    proc = run([
        "kubectl", "-n", NAMESPACE, "get", "configmap", CONFIGMAP,
        "-o", f"jsonpath={{.data.{KEY}}}",
    ])
    if proc.returncode != 0:
        print(f"rollback-rehearsal verification failed: ConfigMap "
              f"'{CONFIGMAP}' not found in namespace '{NAMESPACE}': "
              f"{proc.stderr.strip()}", file=sys.stderr)
        return 1
    script = (proc.stdout or "").strip()
    if len(script) < 20:
        print(f"rollback-rehearsal verification failed: ConfigMap "
              f"'{CONFIGMAP}' key '{KEY}' is missing or too short to be a real "
              f"rollback script (got {len(script)} chars)", file=sys.stderr)
        return 1
    print(f"rollback-rehearsal prepared: ConfigMap '{CONFIGMAP}' has a "
          f"{len(script)}-char '{KEY}' script (not executed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
