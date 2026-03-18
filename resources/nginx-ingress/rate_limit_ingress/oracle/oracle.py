#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import bench_namespace, bench_ns, controller_service_host, pod_exec  # noqa: E402


def _paced_codes(
    namespace: str,
    pod_name: str,
    *,
    host: str,
    url: str,
    request_count: int,
    pause_seconds: str,
) -> list[str]:
    loop = " ".join(str(i) for i in range(1, request_count + 1))
    shell = (
        "for i in "
        + loop
        + "; do curl -s -o /dev/null -w '%{http_code}\\n' -H 'Host: "
        + host
        + "' "
        + url
        + "; sleep "
        + pause_seconds
        + "; done"
    )
    output = pod_exec(namespace, pod_name, ["sh", "-c", shell])
    return [line.strip() for line in output.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--curl-pod-name", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--api-path", required=True)
    parser.add_argument("--expected-limit-status-code", required=True)
    parser.add_argument("--request-count", type=int, required=True)
    parser.add_argument("--request-pause-seconds", required=True)
    parser.add_argument("--min-limited-responses", type=int, required=True)
    args = parser.parse_args()

    app_ns = bench_namespace()
    ingress_ns = bench_ns("ingress", "nginx-ingress")

    controller = controller_service_host(ingress_ns)
    api_codes = _paced_codes(
        app_ns,
        args.curl_pod_name,
        host=args.host,
        url=f"http://{controller}{args.api_path}",
        request_count=args.request_count,
        pause_seconds=args.request_pause_seconds,
    )

    api_limited = api_codes.count(args.expected_limit_status_code)
    api_other = [code for code in api_codes if code not in {args.expected_limit_status_code, "200"}]
    if api_limited < args.min_limited_responses:
        print(
            f"/api returned too few {args.expected_limit_status_code} responses: {api_codes}"
        )
        return 1
    if api_other:
        print(f"/api returned unexpected codes: {api_other}")
        return 1

    print(f"/api codes={api_codes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
