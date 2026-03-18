#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import time
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
    run,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["annotation", "configmap", "telemetry"])
    parser.add_argument("--ingress-name", required=True)
    parser.add_argument("--curl-pod-name", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--collector-service-name", required=True)
    parser.add_argument("--collector-port", required=True)
    args = parser.parse_args()

    app_ns = bench_namespace()
    ingress_ns = bench_ns("ingress", "nginx-ingress")
    otel_ns = bench_ns("otel", "nginx-otel")

    if args.check == "annotation":
        annotations = ingress_annotations(app_ns, args.ingress_name)
        value = annotations.get("nginx.ingress.kubernetes.io/enable-opentelemetry", "")
        if value != "true":
            print(
                f"ingress/{args.ingress_name} enable-opentelemetry annotation={value!r}, expected 'true'"
            )
            return 1
        print(f"ingress/{args.ingress_name} enables OpenTelemetry")
        return 0

    if args.check == "configmap":
        data = configmap_data(ingress_ns, "ingress-nginx-controller")
        collector_host = f"{args.collector_service_name}.{otel_ns}.svc.cluster.local"
        expected_pairs = {
            "enable-opentelemetry": "true",
            "otlp-collector-host": collector_host,
            "otlp-collector-port": str(args.collector_port),
        }
        for key, expected in expected_pairs.items():
            actual = data.get(key, "")
            if actual != expected:
                print(f"configmap/ingress-nginx-controller data[{key!r}]={actual!r}, expected {expected!r}")
                return 1
        log_format = data.get("log-format-upstream", "")
        if "$opentelemetry_trace_id" not in log_format or "$opentelemetry_span_id" not in log_format:
            print("configmap/ingress-nginx-controller log-format-upstream does not include trace/span IDs")
            return 1
        print("configmap/ingress-nginx-controller enables OpenTelemetry and logs trace/span IDs")
        return 0

    nonce = str(int(time.time() * 1000))
    request_path = f"{args.path}?trace_nonce={nonce}"
    body = pod_exec(
        app_ns,
        args.curl_pod_name,
        [
            "curl",
            "-sS",
            "-H",
            f"Host: {args.host}",
            f"http://{controller_service_host(ingress_ns)}{request_path}",
        ],
    ).strip()
    if body != "hello":
        print(f"telemetry probe request returned {body!r}, expected 'hello'")
        return 1

    time.sleep(4)

    controller_logs = run(
        [
            "kubectl",
            "-n",
            ingress_ns,
            "logs",
            "deploy/ingress-nginx-controller",
            "--tail=400",
        ],
        check=True,
    ).stdout
    trace_re = re.compile(r"otel_trace_id=([0-9a-f]{32})")
    span_re = re.compile(r"otel_span_id=([0-9a-f]{16})")
    trace_id = ""
    span_id = ""
    for line in controller_logs.splitlines():
        if f"trace_nonce={nonce}" not in line:
            continue
        trace_match = trace_re.search(line)
        span_match = span_re.search(line)
        if trace_match and span_match:
            trace_id = trace_match.group(1)
            span_id = span_match.group(1)
            break
    if not trace_id or not span_id:
        print("controller logs are missing non-empty trace/span IDs for the probe request")
        return 1

    collector_logs = run(
        [
            "kubectl",
            "-n",
            otel_ns,
            "logs",
            "deploy/otel-collector",
            "--since=180s",
        ],
        check=True,
    ).stdout
    if trace_id.lower() not in collector_logs.lower():
        print(f"collector logs do not contain trace ID {trace_id}")
        return 1

    print(f"controller log captured trace_id={trace_id} span_id={span_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
