#!/usr/bin/env python3
import subprocess
import sys


UI_HOST = "crdb-ui.example.com"
INGRESS_HTTP_URL = "http://ingress-nginx-controller.ingress-nginx.svc/"
SQL_HOST = "ingress-nginx-controller.ingress-nginx.svc"
SQL_PORT = "26257"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def check_ui(errors):
    cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
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
        f"Host: {UI_HOST}",
        INGRESS_HTTP_URL,
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


def check_sql(errors):
    cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "crdb-cluster-0",
        "--",
        "./cockroach",
        "sql",
        "--insecure",
        "--host",
        SQL_HOST,
        "--port",
        SQL_PORT,
        "-e",
        "SELECT 1;",
    ]
    result = run(cmd)
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        errors.append(msg or "SQL query through ingress failed")


def main():
    errors = []

    check_ui(errors)
    check_sql(errors)

    if errors:
        print("Expose ingress verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("UI and SQL traffic verified through ingress-nginx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
