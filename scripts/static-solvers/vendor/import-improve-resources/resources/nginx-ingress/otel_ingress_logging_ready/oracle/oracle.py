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

    app_ns = bench_ns("app", "nginx-app")
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

    trace_re = re.compile(r"otel_trace_id=([0-9a-f]{32})")
    span_re = re.compile(r"otel_span_id=([0-9a-f]{16})")

    seen_traces: set[str] = set()
    reached_backend = False
    request_failure = ""
    deadline = time.monotonic() + 45

    while True:
        nonce = str(int(time.time() * 1000))
        request_path = f"{args.path}?trace_nonce={nonce}"
        try:
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
        except Exception as exc:  # noqa: BLE001
            request_failure = str(exc)
        else:
            if body:
                reached_backend = True

        time.sleep(2)

        controller_logs = run(
            [
                "kubectl",
                "-n",
                ingress_ns,
                "logs",
                "deploy/ingress-nginx-controller",
                "--tail=600",
            ],
            check=True,
        ).stdout
        for line in controller_logs.splitlines():
            if args.path not in line:
                continue
            trace_match = trace_re.search(line)
            span_match = span_re.search(line)
            if trace_match and span_match:
                seen_traces.add(trace_match.group(1).lower())

        if seen_traces:
            collector_logs = run(
                [
                    "kubectl",
                    "-n",
                    otel_ns,
                    "logs",
                    f"deploy/{args.collector_service_name}",
                    "--since=180s",
                ],
                check=True,
            ).stdout.lower()
            for trace_id in seen_traces:
                if trace_id in collector_logs:
                    print(f"collector captured trace_id={trace_id}")
                    return 0

        if time.monotonic() >= deadline:
            break
        time.sleep(3)

    if not reached_backend:
        if request_failure:
            print(f"failed to send telemetry probe request: {request_failure}")
        else:
            print("telemetry probe request returned an empty body")
        return 1
    if not seen_traces:
        print("controller logs are missing non-empty trace/span IDs for probe requests")
        return 1

    print(f"collector logs do not contain any observed trace IDs: {sorted(seen_traces)!r}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
