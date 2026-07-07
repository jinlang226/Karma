#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import bench_namespace, curl_dashboard_status, names_from_env, service_ports  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["service-port", "http"])
    parser.add_argument("--expected-port", type=int, required=True)
    args = parser.parse_args()

    ns = bench_namespace()
    names = names_from_env()

    if args.check == "service-port":
        ports = service_ports(ns, names.head)
        if args.expected_port not in ports:
            print(f"service/{names.head} does not expose port {args.expected_port}")
            return 1
        print(f"service/{names.head} exposes port {args.expected_port}")
        return 0

    deadline = time.monotonic() + 90
    last_detail = ""
    while time.monotonic() < deadline:
        try:
            status = curl_dashboard_status(
                ns,
                names.curl_test,
                names.head,
                args.expected_port,
            )
        except RuntimeError as exc:
            last_detail = str(exc)
        else:
            last_detail = f"HTTP status {status}"
            if status == "200":
                print("dashboard endpoint returned HTTP 200")
                return 0
        time.sleep(3)
    print(f"dashboard endpoint did not become ready: {last_detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
