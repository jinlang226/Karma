#!/usr/bin/env python3
"""Oracle for ray/dashboard_exposure.

Verifies the ray-head Service exposes the dashboard port and that the dashboard
endpoint returns HTTP 200 from inside the cluster.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from common.oracle_lib import curl_dashboard_status, service_ports  # noqa: E402

NAMESPACE = "ray"
HEAD = "ray-head"
CURL_POD = "curl-test"
# Param-aware: a workflow can override dashboard_port. Read it from the env
# (default = the standalone value 8265) so the oracle checks whichever port the
# workflow asked the agent to expose.
EXPECTED_PORT = int(os.environ.get("BENCH_PARAM_DASHBOARD_PORT", "8265") or "8265")


def check_service_port() -> int:
    """Confirm the head Service exposes the dashboard port."""
    ports = service_ports(NAMESPACE, HEAD)
    if EXPECTED_PORT not in ports:
        print(f"service/{HEAD} does not expose port {EXPECTED_PORT}")
        return 1
    print(f"service/{HEAD} exposes port {EXPECTED_PORT}")
    return 0


def check_http() -> int:
    """Confirm the dashboard endpoint returns HTTP 200."""
    status = curl_dashboard_status(NAMESPACE, CURL_POD, HEAD, EXPECTED_PORT)
    if status != "200":
        print(f"dashboard HTTP status {status}, expected 200")
        return 1
    print("dashboard endpoint returned HTTP 200")
    return 0


def main() -> int:
    """Run every dashboard_exposure verification check in order."""
    for fn in (check_service_port, check_http):
        rc = fn()
        if rc != 0:
            return rc
    print("ray dashboard_exposure verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
