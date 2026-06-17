#!/usr/bin/env python3
# Verify Prometheus has an active target scraping CockroachDB metrics and the
# metrics endpoint is reachable. The metrics path (BENCH_PARAM_METRICS_PATH) and
# port (BENCH_PARAM_METRICS_PORT) come from the case params, so a workflow that
# overrides them is honored instead of a hardcoded value. Standalone (default
# params) this behaves identically.
import json
import os
import subprocess
import sys
from urllib.parse import urlparse


METRICS_PATH = os.environ.get("BENCH_PARAM_METRICS_PATH", "/_status/vars")
METRICS_PORT = os.environ.get("BENCH_PARAM_METRICS_PORT", "8080")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def fetch_metrics(host, port, path):
    """Fetch the CockroachDB metrics endpoint, scheme-adaptively.

    Standalone the cluster is `--insecure` and serves :8080 over plain HTTP. But
    in a workflow this stage can inherit a SECURE cluster (e.g. after
    certificate-rotation / generate-cert), which serves :8080 over HTTPS only and
    307-redirects a plain-HTTP request to https (whose body has no metrics). Try
    HTTPS first with -k (self-signed) and -L (follow redirects), then fall back to
    HTTP, and return whichever yields real metrics output. Workflow-agnostic.
    """
    for scheme, extra in (("https", ["-k", "-L"]), ("http", [])):
        cmd = [
            "kubectl", "-n", "cockroachdb", "exec", "curl-test", "--",
            "curl", "-fsS", *extra, f"{scheme}://{host}:{port}{path}",
        ]
        result = run(cmd)
        if result.returncode == 0 and "sys_uptime" in result.stdout:
            return result, ""
    # Neither scheme produced metrics; surface the last attempt's error.
    return None, (result.stderr.strip() or "Metrics endpoint unreachable")


def parse_targets(payload):
    data = payload.get("data", {})
    return data.get("activeTargets", [])


def load_pod_ips():
    cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "get",
        "pods",
        "-l",
        "app.kubernetes.io/name=cockroachdb",
        "-o",
        "json",
    ]
    result = run(cmd)
    if result.returncode != 0:
        return None, result.stderr.strip()
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "Failed to parse pod list"
    ips = []
    for item in data.get("items", []):
        ip = item.get("status", {}).get("podIP")
        if ip:
            ips.append(ip)
    return ips, ""


def main():
    errors = []

    pod_ips, pod_err = load_pod_ips()
    if pod_ips is None:
        errors.append(pod_err or "Failed to load pod IPs")
        pod_ips = []

    cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "curl-test",
        "--",
        "curl",
        "-fsS",
        "http://prometheus.monitoring.svc:9090/api/v1/targets",
    ]
    result = run(cmd)
    if result.returncode != 0:
        errors.append(f"Failed to query Prometheus targets: {result.stderr.strip()}")
    else:
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            errors.append("Failed to parse Prometheus targets")
            payload = {}

        targets = parse_targets(payload)
        target_ok = False
        for target in targets:
            if target.get("health") != "up":
                continue
            scrape_url = target.get("scrapeUrl", "")
            parsed = urlparse(scrape_url)
            if parsed.path != METRICS_PATH:
                continue
            if str(parsed.port) != str(METRICS_PORT):
                continue
            if parsed.hostname in pod_ips:
                target_ok = True
                break

        if not target_ok:
            errors.append(
                f"No active Prometheus target scraping {METRICS_PATH} on port {METRICS_PORT}"
            )

    result, fetch_err = fetch_metrics("crdb-cluster-public", METRICS_PORT, METRICS_PATH)
    if result is None:
        errors.append(f"Metrics endpoint unreachable: {fetch_err}")
    elif "sys_uptime" not in result.stdout:
        errors.append("Metrics output missing sys_uptime")

    if errors:
        print("Monitoring integration verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Monitoring integration configured successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
