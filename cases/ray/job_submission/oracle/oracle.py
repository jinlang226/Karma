#!/usr/bin/env python3
"""Oracle for ray/job_submission.

Runs the fixed job script inside the ray-client pod and verifies it connects to
the cluster, exits 0, and prints the expected output.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from common.oracle_lib import run  # noqa: E402

NAMESPACE = "ray"
CLIENT = "ray-client"
JOB_PATH = "/opt/job.py"
# Param-aware: a workflow can override expected_output. Read it from the env
# (default = the standalone value "pong") so the oracle checks whichever string
# the workflow told the agent to make the job print.
EXPECTED_OUTPUT = os.environ.get("BENCH_PARAM_EXPECTED_OUTPUT", "pong") or "pong"


def check_job() -> int:
    """Execute the job script in ray-client and check its exit code + output."""
    proc = run(
        ["kubectl", "-n", NAMESPACE, "exec", CLIENT, "--", "python", JOB_PATH]
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        print(f"job script failed in {CLIENT}: {detail}")
        return 1
    output_lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if EXPECTED_OUTPUT not in output_lines:
        print(f"job script output {output_lines!r}, expected to contain {EXPECTED_OUTPUT!r}")
        return 1
    print(f"job script printed {EXPECTED_OUTPUT!r}")
    return 0


def main() -> int:
    """Run the job_submission verification check."""
    rc = check_job()
    if rc != 0:
        return rc
    print("ray job_submission verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
