#!/usr/bin/env python3
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.append(str(Path(__file__).resolve().parents[2]))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_param,
    bench_param_int,
    cluster_prefix,
    run,
)


def parse_targets(payload):
    data = payload.get("data", {})
    return data.get("activeTargets", [])


def load_pod_ips(namespace):
    cmd = [
        "kubectl",
        "-n",
        namespace,
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
    namespace = bench_namespace("cockroachdb")
    prefix = cluster_prefix("crdb-cluster")
    metrics_path = bench_param("metrics_path", "/_status/vars")
    metrics_port = bench_param_int("metrics_port", 8080)
    prometheus_service_name = bench_param("prometheus_service_name", "prometheus")
    prometheus_service_port = bench_param_int("prometheus_service_port", 9090)
    prometheus_namespace = "monitoring"

    if not metrics_path.startswith("/"):
        metrics_path = f"/{metrics_path}"

    errors = []

    pod_ips, pod_err = load_pod_ips(namespace)
    if pod_ips is None:
        errors.append(pod_err or "Failed to load pod IPs")
        pod_ips = []

    prom_url = (
        f"http://{prometheus_service_name}.{prometheus_namespace}.svc:"
        f"{prometheus_service_port}/api/v1/targets"
    )
    prom_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        "curl-test",
        "--",
        "curl",
        "-fsS",
        prom_url,
    ]
    prom_result = run(prom_cmd)
    if prom_result.returncode != 0:
        errors.append(f"Failed to query Prometheus targets: {prom_result.stderr.strip()}")
    else:
        try:
            payload = json.loads(prom_result.stdout)
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
            if parsed.path != metrics_path:
                continue
            if str(parsed.port) != str(metrics_port):
                continue
            if parsed.hostname in pod_ips:
                target_ok = True
                break

        if not target_ok:
            errors.append(
                f"No active Prometheus target scraping {metrics_path} on port {metrics_port}"
            )

    metrics_url = f"http://{prefix}-public:{metrics_port}{metrics_path}"
    metrics_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        "curl-test",
        "--",
        "curl",
        "-fsS",
        metrics_url,
    ]
    metrics_result = run(metrics_cmd)
    if metrics_result.returncode != 0:
        errors.append(f"Metrics endpoint unreachable: {metrics_result.stderr.strip()}")
    elif "sys_uptime" not in metrics_result.stdout:
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
