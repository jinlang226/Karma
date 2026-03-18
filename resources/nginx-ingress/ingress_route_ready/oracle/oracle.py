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
    controller_service_host,
    ingress_class_name,
    ingress_paths,
    pod_exec,
    service_ports,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["service", "ingress", "http"])
    parser.add_argument("--service-name", required=True)
    parser.add_argument("--ingress-name", required=True)
    parser.add_argument("--curl-pod-name", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--expected-service-port", type=int, required=True)
    parser.add_argument("--expected-ingress-class", required=True)
    parser.add_argument("--expected-body", required=True)
    args = parser.parse_args()

    app_ns = bench_namespace()
    ingress_ns = bench_ns("ingress", "nginx-ingress")

    if args.check == "service":
        ports = service_ports(app_ns, args.service_name)
        if args.expected_service_port not in ports:
            print(
                f"service/{args.service_name} exposes ports {sorted(ports)}, "
                f"expected {args.expected_service_port}"
            )
            return 1
        print(f"service/{args.service_name} exposes port {args.expected_service_port}")
        return 0

    if args.check == "ingress":
        class_name = ingress_class_name(app_ns, args.ingress_name)
        if class_name != args.expected_ingress_class:
            print(
                f"ingress/{args.ingress_name} ingressClassName={class_name!r}, "
                f"expected {args.expected_ingress_class!r}"
            )
            return 1
        paths = ingress_paths(app_ns, args.ingress_name, host=args.host)
        if (args.host, args.path, args.service_name) not in paths:
            print(
                f"ingress/{args.ingress_name} rules {paths!r} do not include "
                f"({args.host!r}, {args.path!r}, {args.service_name!r})"
            )
            return 1
        print(f"ingress/{args.ingress_name} routes {args.host}{args.path} to {args.service_name}")
        return 0

    body = pod_exec(
        app_ns,
        args.curl_pod_name,
        [
            "curl",
            "-sS",
            "-H",
            f"Host: {args.host}",
            f"http://{controller_service_host(ingress_ns)}{args.path}",
        ],
    ).strip()
    if body != args.expected_body:
        print(f"route returned {body!r}, expected {args.expected_body!r}")
        return 1
    print(f"route returned {body!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
