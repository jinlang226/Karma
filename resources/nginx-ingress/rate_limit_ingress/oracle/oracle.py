#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_ns,
    configmap_data,
    controller_service_host,
    ingress_annotations,
    pod_exec,
)


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
    parser.add_argument("--check", required=True, choices=["annotations", "configmap", "behavior"])
    parser.add_argument("--api-ingress-name", required=True)
    parser.add_argument("--curl-pod-name", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--api-path", required=True)
    parser.add_argument("--health-path", required=True)
    parser.add_argument("--expected-limit-rps", required=True)
    parser.add_argument("--expected-limit-burst", required=True)
    parser.add_argument("--expected-limit-status-code", required=True)
    parser.add_argument("--request-count", type=int, required=True)
    parser.add_argument("--request-pause-seconds", required=True)
    parser.add_argument("--min-limited-responses", type=int, required=True)
    args = parser.parse_args()

    app_ns = bench_namespace()
    ingress_ns = bench_ns("ingress", "nginx-ingress")

    if args.check == "annotations":
        annotations = ingress_annotations(app_ns, args.api_ingress_name)
        expected = {
            "nginx.ingress.kubernetes.io/limit-rps": args.expected_limit_rps,
            "nginx.ingress.kubernetes.io/limit-burst": args.expected_limit_burst,
        }
        for key, value in expected.items():
            actual = annotations.get(key, "")
            if actual != value:
                print(
                    f"ingress/{args.api_ingress_name} annotation {key}={actual!r}, "
                    f"expected {value!r}"
                )
                return 1
        print(f"ingress/{args.api_ingress_name} has expected rate-limit annotations")
        return 0

    if args.check == "configmap":
        data = configmap_data(ingress_ns, "ingress-nginx-controller")
        actual = data.get("limit-req-status-code", "")
        if actual != args.expected_limit_status_code:
            print(
                "configmap/ingress-nginx-controller data.limit-req-status-code="
                f"{actual!r}, expected {args.expected_limit_status_code!r}"
            )
            return 1
        print("configmap/ingress-nginx-controller has expected limit-req-status-code")
        return 0

    controller = controller_service_host(ingress_ns)
    api_codes = _paced_codes(
        app_ns,
        args.curl_pod_name,
        host=args.host,
        url=f"http://{controller}{args.api_path}",
        request_count=args.request_count,
        pause_seconds=args.request_pause_seconds,
    )
    health_codes = _paced_codes(
        app_ns,
        args.curl_pod_name,
        host=args.host,
        url=f"http://{controller}{args.health_path}",
        request_count=args.request_count,
        pause_seconds=args.request_pause_seconds,
    )

    api_200 = api_codes.count("200")
    api_limited = api_codes.count(args.expected_limit_status_code)
    api_other = [code for code in api_codes if code not in {args.expected_limit_status_code, "200"}]
    if api_limited < args.min_limited_responses:
        print(
            f"/api returned too few {args.expected_limit_status_code} responses: {api_codes}"
        )
        return 1
    if api_200 < 1:
        print(f"/api did not return any 200 responses: {api_codes}")
        return 1
    if api_other:
        print(f"/api returned unexpected codes: {api_other}")
        return 1

    health_other = [code for code in health_codes if code != "200"]
    if health_other:
        print(f"/health returned non-200 codes: {health_other}")
        return 1

    print(f"/api codes={api_codes}; /health codes={health_codes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
