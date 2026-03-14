#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_param,
    bench_param_int,
    cluster_pod,
    cluster_prefix,
    run,
)


def check_ui(namespace, ui_host, ingress_http_url, errors):
    cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        "curl-test",
        "--",
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "-H",
        f"Host: {ui_host}",
        ingress_http_url,
    ]
    result = run(cmd)
    if result.returncode != 0:
        errors.append(result.stderr.strip() or "Failed to curl UI through ingress")
        return
    code = result.stdout.strip()
    if not code.isdigit():
        errors.append(f"Unexpected HTTP status output: {code}")
        return
    status = int(code)
    if status < 200 or status >= 400:
        errors.append(f"UI ingress returned HTTP {status}")


def check_sql(namespace, pod_name, sql_host, sql_port, errors):
    cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod_name,
        "--",
        "./cockroach",
        "sql",
        "--insecure",
        "--host",
        sql_host,
        "--port",
        str(sql_port),
        "-e",
        "SELECT 1;",
    ]
    result = run(cmd)
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        errors.append(msg or "SQL query through ingress failed")


def main():
    namespace = bench_namespace("cockroachdb")
    prefix = cluster_prefix("crdb-cluster")
    pod0 = cluster_pod(prefix, 0)

    ui_host = bench_param("ui_host", "crdb-ui.example.com")
    sql_port = bench_param_int("sql_port", 26257)
    ingress_http_url = "http://ingress-nginx-controller.ingress-nginx.svc/"
    sql_host = "ingress-nginx-controller.ingress-nginx.svc"

    errors = []

    check_ui(namespace, ui_host, ingress_http_url, errors)
    check_sql(namespace, pod0, sql_host, sql_port, errors)

    if errors:
        print("Expose ingress verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("UI and SQL traffic verified through ingress")
    return 0


if __name__ == "__main__":
    sys.exit(main())
