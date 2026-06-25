#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
MONITORING_NAMESPACE = os.environ.get("BENCH_NS_MONITORING", "monitoring")
PROMETHEUS_SERVICE_NAME = os.environ.get("BENCH_PARAM_PROMETHEUS_SERVICE_NAME", "prometheus")
PROMETHEUS_SERVICE_PORT = int(os.environ.get("BENCH_PARAM_PROMETHEUS_SERVICE_PORT", "9090"))
CURL_POD_NAME = "curl-test"
METRICS_QUERY = os.environ.get("BENCH_PARAM_METRICS_QUERY", "mongodb_up")
METRICS_PORT = int(os.environ.get("BENCH_PARAM_METRICS_PORT", "9216"))
METRICS_PATH = os.environ.get("BENCH_PARAM_METRICS_PATH", "/metrics")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def fail(prefix, errors):
    if errors:
        print(prefix, file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


def prometheus_request(path, errors):
    url = f"http://{PROMETHEUS_SERVICE_NAME}.{MONITORING_NAMESPACE}.svc:{PROMETHEUS_SERVICE_PORT}{path}"
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        CURL_POD_NAME,
        "--",
        "curl",
        "-fsS",
        url,
    ]
    res = run(cmd)
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        errors.append(f"Prometheus request failed for {path}: {detail}")
        return None
    return (res.stdout or "").strip()


def check_connectivity():
    errors = []
    ready = prometheus_request("/-/ready", errors)
    if ready is not None and "ready" not in ready.lower():
        errors.append("Prometheus readiness endpoint did not return ready")
    return fail("Monitoring integration connectivity check failed:", errors)


def check_targets():
    expected_suffix = f":{METRICS_PORT}{METRICS_PATH}"
    deadline = time.monotonic() + 120
    errors = []
    while True:
        attempt_errors = []
        raw = prometheus_request("/api/v1/targets", attempt_errors)
        if raw is not None:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                attempt_errors.append("Unable to parse Prometheus targets JSON response")
            else:
                if payload.get("status") != "success":
                    attempt_errors.append(f"Prometheus targets status={payload.get('status')}")
                else:
                    active_targets = payload.get("data", {}).get("activeTargets", [])
                    if any(
                        target.get("health") == "up"
                        and str(target.get("scrapeUrl") or "").endswith(expected_suffix)
                        for target in active_targets
                    ):
                        return 0
                    attempt_errors.append(
                        f"No healthy scrape target ends with {expected_suffix}"
                    )
        errors = attempt_errors
        if time.monotonic() >= deadline:
            break
        time.sleep(5)

    return fail("Monitoring integration targets check failed:", errors)


def check_metric():
    encoded = urllib.parse.quote(METRICS_QUERY, safe="")
    deadline = time.monotonic() + 120
    errors = []
    while True:
        attempt_errors = []
        raw = prometheus_request(f"/api/v1/query?query={encoded}", attempt_errors)
        if raw is not None:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                attempt_errors.append("Unable to parse Prometheus query JSON response")
            else:
                if payload.get("status") != "success":
                    attempt_errors.append(
                        f"Prometheus query status={payload.get('status')}"
                    )
                else:
                    for series in payload.get("data", {}).get("result", []):
                        value = series.get("value", [])
                        if len(value) < 2:
                            continue
                        try:
                            if float(value[1]) >= 1:
                                return 0
                        except ValueError:
                            continue
                    attempt_errors.append(f"{METRICS_QUERY} missing or < 1")
        errors = attempt_errors
        if time.monotonic() >= deadline:
            break
        time.sleep(5)

    return fail("Monitoring integration metric check failed:", errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", default="all", choices=["all", "connectivity", "targets", "metric"])
    args = parser.parse_args()

    if args.check == "connectivity":
        return check_connectivity()
    if args.check == "targets":
        return check_targets()
    if args.check == "metric":
        return check_metric()

    for fn in (check_connectivity, check_targets, check_metric):
        rc = fn()
        if rc != 0:
            return rc
    print("Monitoring integration verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
