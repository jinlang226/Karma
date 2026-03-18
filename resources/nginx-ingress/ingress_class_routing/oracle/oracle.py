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
    deployment_args,
    ingress_class_name,
    ingress_paths,
    pod_exec,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["controller-flag", "ingress", "http"])
    parser.add_argument("--ingress-name", required=True)
    parser.add_argument("--curl-pod-name", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--service-name", required=True)
    parser.add_argument("--expected-ingress-class", required=True)
    parser.add_argument("--expected-body", required=True)
    args = parser.parse_args()

    app_ns = bench_namespace()
    ingress_ns = bench_ns("ingress", "nginx-ingress")

    if args.check == "controller-flag":
        flags = deployment_args(ingress_ns, "ingress-nginx-controller")
        if "--watch-ingress-without-class=false" not in flags:
            print(
                "deployment/ingress-nginx-controller does not require explicit ingress classes: "
                f"{flags!r}"
            )
            return 1
        print("deployment/ingress-nginx-controller requires explicit ingress classes")
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
        print(f"ingress/{args.ingress_name} binds class {class_name!r} and routes correctly")
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
