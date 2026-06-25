#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import (  # noqa: E402
    bench_ns,
    controller_service_host,
    deployment_args,
    ingress_class_name,
    ingress_paths,
    pod_exec,
)


def wait_for_body(
    namespace: str,
    pod_name: str,
    command: list[str],
    expected: str,
    *,
    attempts: int = 20,
    interval_sec: int = 2,
) -> str:
    last_body = ""
    last_error = ""
    for attempt in range(attempts):
        try:
            last_body = pod_exec(namespace, pod_name, command).strip()
            if last_body == expected:
                return last_body
            last_error = ""
        except RuntimeError as exc:
            last_error = str(exc)
        if attempt + 1 < attempts:
            time.sleep(interval_sec)
    detail = f"last body {last_body!r}" if not last_error else f"last error: {last_error}"
    raise RuntimeError(
        f"route did not return {expected!r} after {attempts} attempts; {detail}"
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

    app_ns = bench_ns("app", "nginx-app")
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

    command = [
        "curl",
        "-sS",
        "-H",
        f"Host: {args.host}",
        f"http://{controller_service_host(ingress_ns)}{args.path}",
    ]
    try:
        body = wait_for_body(
            app_ns,
            args.curl_pod_name,
            command,
            args.expected_body,
        )
    except RuntimeError as exc:
        print(f"route failed: {exc}")
        return 1
    print(f"route returned {body!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
