#!/usr/bin/env python3
import json
import subprocess
import sys
from urllib.parse import urlparse


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
            if parsed.path != "/_status/vars":
                continue
            if parsed.port not in (8080, "8080"):
                continue
            if parsed.hostname in pod_ips:
                target_ok = True
                break

        if not target_ok:
            errors.append("No active Prometheus target scraping /_status/vars on port 8080")

    cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "curl-test",
        "--",
        "curl",
        "-fsS",
        "http://crdb-cluster-public:8080/_status/vars",
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
