#!/usr/bin/env python3
"""Oracle for nginx-ingress/readonly-audit.

Verifies the agent wrote compliance audit findings as a ConfigMap WITHOUT making
any cluster changes: the `config-audit` ConfigMap must exist with a non-empty
`findings.txt` key. Whether the agent (wrongly) modified resources and reverted
earlier stages is caught separately by the workflow's regression sweep.
"""
from __future__ import annotations

import subprocess
import sys

NAMESPACE = "demo"
CONFIGMAP = "config-audit"
KEY = "findings.txt"


def run(cmd):
    """Run a subprocess command and return the completed process."""
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main() -> int:
    """Check the config-audit ConfigMap exists with non-empty findings."""
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
              f"'{CONFIGMAP}' key '{KEY}' is missing or too short to be real "
              f"audit findings (got {len(findings)} chars)", file=sys.stderr)
        return 1
    print(f"readonly-audit complete: ConfigMap '{CONFIGMAP}' has "
          f"{len(findings)}-char '{KEY}' findings (no changes made).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
