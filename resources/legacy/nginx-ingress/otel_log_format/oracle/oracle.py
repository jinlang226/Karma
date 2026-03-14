#!/usr/bin/env python3
import re
import subprocess
import sys
import time


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main():
    request_cmd = [
        "kubectl",
        "-n",
        "demo",
        "exec",
        "curl-test",
        "--",
        "curl",
        "-sS",
        "-H",
        "Host: otel.example.com",
        "http://ingress-nginx-controller.ingress-nginx.svc/otel-check",
    ]
    result = run(request_cmd)
    if result.returncode != 0:
        print("Failed to send request through ingress", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return 1

    body = result.stdout.strip()
    if body and body != "hello":
        print(f"Unexpected response body: {body}", file=sys.stderr)
        return 1

    time.sleep(4)

    logs_cmd = [
        "kubectl",
        "-n",
        "ingress-nginx",
        "logs",
        "deploy/ingress-nginx-controller",
        "--tail=200",
    ]
    logs = run(logs_cmd)
    if logs.returncode != 0:
        print("Failed to read ingress-nginx logs", file=sys.stderr)
        if logs.stderr:
            print(logs.stderr.strip(), file=sys.stderr)
        return 1

    trace_re = re.compile(r"otel_trace_id=([0-9a-f]{32})")
    span_re = re.compile(r"otel_span_id=([0-9a-f]{16})")
    trace_id = None
    span_id = None
    for line in logs.stdout.splitlines():
        if "/otel-check" not in line:
            continue
        trace_match = trace_re.search(line)
        span_match = span_re.search(line)
        if trace_match and span_match:
            trace_id = trace_match.group(1)
            span_id = span_match.group(1)
            break

    if not trace_id or not span_id:
        print("Missing otel_trace_id or otel_span_id in access logs", file=sys.stderr)
        return 1

    collector_cmd = [
        "kubectl",
        "-n",
        "otel",
        "logs",
        "deploy/otel-collector",
        "--since=120s",
    ]
    collector_logs = run(collector_cmd)
    if collector_logs.returncode != 0:
        print("Failed to read otel-collector logs", file=sys.stderr)
        if collector_logs.stderr:
            print(collector_logs.stderr.strip(), file=sys.stderr)
        return 1

    if trace_id.lower() in collector_logs.stdout.lower():
        return 0

    print("Trace ID not found in otel-collector logs", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
