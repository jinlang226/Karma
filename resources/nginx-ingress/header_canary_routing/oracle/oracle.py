#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import bench_namespace, bench_ns, controller_service_host, ingress_annotations, pod_exec  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["annotations", "stable", "canary"])
    parser.add_argument("--curl-pod-name", required=True)
    parser.add_argument("--canary-ingress-name", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--header-name", required=True)
    parser.add_argument("--header-value", required=True)
    parser.add_argument("--expected-stable-body", required=True)
    parser.add_argument("--expected-canary-body", required=True)
    args = parser.parse_args()

    app_ns = bench_namespace()
    ingress_ns = bench_ns("ingress", "nginx-ingress")

    if args.check == "annotations":
        annotations = ingress_annotations(app_ns, args.canary_ingress_name)
        expected = {
            "nginx.ingress.kubernetes.io/canary": "true",
            "nginx.ingress.kubernetes.io/canary-by-header": args.header_name,
            "nginx.ingress.kubernetes.io/canary-by-header-value": args.header_value,
        }
        for key, value in expected.items():
            actual = annotations.get(key, "")
            if actual != value:
                print(
                    f"ingress/{args.canary_ingress_name} annotation {key}={actual!r}, "
                    f"expected {value!r}"
                )
                return 1
        print(f"ingress/{args.canary_ingress_name} has expected canary annotations")
        return 0

    curl_cmd = [
        "curl",
        "-sS",
        "-H",
        f"Host: {args.host}",
    ]
    if args.check == "canary":
        curl_cmd.extend(["-H", f"{args.header_name}: {args.header_value}"])
    curl_cmd.append(f"http://{controller_service_host(ingress_ns)}{args.path}")
    body = pod_exec(app_ns, args.curl_pod_name, curl_cmd).strip()
    expected = args.expected_stable_body if args.check == "stable" else args.expected_canary_body
    if body != expected:
        print(f"{args.check} request returned {body!r}, expected {expected!r}")
        return 1
    print(f"{args.check} request returned {body!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
