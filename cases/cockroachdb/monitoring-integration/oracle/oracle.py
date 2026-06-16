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

    cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "curl-test",
        "--",
        "curl",
        "-fsS",
        f"http://crdb-cluster-public:{METRICS_PORT}{METRICS_PATH}",
    ]
    result = run(cmd)
    if result.returncode != 0:
        errors.append(f"Metrics endpoint unreachable: {result.stderr.strip()}")
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
