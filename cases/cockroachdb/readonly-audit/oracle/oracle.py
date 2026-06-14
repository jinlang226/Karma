#!/usr/bin/env python3
"""Oracle for cockroachdb/readonly-audit.

Verifies the agent produced a compliance audit report as a ConfigMap WITHOUT
making any cluster changes: the `config-audit` ConfigMap must exist with a
non-empty `findings.txt` key. Whether the agent (wrongly) mutated cluster
state during the audit is caught separately by the workflow's regression sweep,
which re-runs earlier stages' oracles against the final cluster state.
"""
from __future__ import annotations

import subprocess
import sys

NAMESPACE = "cockroachdb"
CONFIGMAP = "config-audit"
KEY = "findings.txt"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main() -> int:
    """Check the config-audit ConfigMap exists with a non-empty findings document."""
    proc = run([
        "kubectl", "-n", NAMESPACE, "get", "configmap", CONFIGMAP,
        "-o", "jsonpath={.data." + KEY.replace(".", "\\.") + "}",
    ])
    if proc.returncode != 0:
        print(f"readonly-audit verification failed: ConfigMap "
              f"'{CONFIGMAP}' not found in namespace '{NAMESPACE}': "
              f"{proc.stderr.strip()}", file=sys.stderr)
        return 1
    findings = (proc.stdout or "").strip()
    if len(findings) < 20:
        print(f"readonly-audit verification failed: ConfigMap "
              f"'{CONFIGMAP}' key '{KEY}' is missing or too short to be a real "
              f"audit report (got {len(findings)} chars)", file=sys.stderr)
        return 1
    print(f"readonly-audit complete: ConfigMap '{CONFIGMAP}' has a "
          f"{len(findings)}-char '{KEY}' report (no cluster changes made).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
