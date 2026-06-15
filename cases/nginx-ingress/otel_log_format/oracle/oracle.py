#!/usr/bin/env python3
import os
import re
import subprocess
import sys
import time

# Param-aware: a workflow can override host/path via param_overrides; read
# BENCH_PARAM_* (default = the standalone value) so the oracle generates and
# matches the access-log line for the path this stage was asked to trace on the
# live cluster. The OTel/log-format pass criterion is unchanged.
HOST = os.environ.get("BENCH_PARAM_HOST") or "otel.example.com"
PATH = os.environ.get("BENCH_PARAM_PATH") or "/otel-check"


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
        f"Host: {HOST}",
        f"http://ingress-nginx-controller.ingress-nginx.svc{PATH}",
    ]
    result = run(request_cmd)
    if result.returncode != 0:
        print("Failed to send request through ingress", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return 1

    # The request must reach a real backend (non-empty response), but the exact
    # body text is NOT part of this case's criterion — it verifies the OTel log
    # FORMAT, not the upstream's payload. Standalone the echo backend returns
    # "hello"; in a workflow the persistence invariant means this stage inherits
    # whatever backend a prior stage left (e.g. "otel-echo-ok"), and the
    # precondition correctly skips redeploying it. Asserting a fixed string here
    # would falsely fail that valid inherited state. The OTel trace/span/collector
    # checks below are the unchanged, authoritative pass criterion.
    body = result.stdout.strip()
    if not body:
        print("Empty response body — request did not reach a backend", file=sys.stderr)
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
        if PATH not in line:
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
