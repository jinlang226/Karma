#!/usr/bin/env python3
# Verify the DB Console + SQL are reachable through ingress-nginx. The UI host
# (BENCH_PARAM_UI_HOST) and SQL port (BENCH_PARAM_SQL_PORT) come from the case
# params, so a workflow that overrides them is honored instead of a hardcoded
# value. Standalone (default params) this behaves identically.
import os
import subprocess
import sys


UI_HOST = os.environ.get("BENCH_PARAM_UI_HOST", "crdb-ui.example.com")
INGRESS_HTTP_URL = "http://ingress-nginx-controller.ingress-nginx.svc/"
SQL_HOST = "ingress-nginx-controller.ingress-nginx.svc"
SQL_PORT = os.environ.get("BENCH_PARAM_SQL_PORT", "26257")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


_CONN_FLAG = None


def conn_flag():
    """Return the right cockroach SQL connection flag for the live cluster.

    Standalone this case runs against an INSECURE cluster (`--insecure`). But in
    a workflow this stage can inherit a SECURE cluster left running by a prior
    stage (e.g. certificate-rotation), whose precondition probe sees pods already
    Running and skips its own insecure redeploy. A hardcoded `--insecure` then
    fails with an SSL authentication error. Detect the mode once by checking for
    the mounted certs dir and connect accordingly so the same oracle works in
    both contexts. Mirrors cockroachdb/cluster-settings/oracle/oracle.py.
    """
    global _CONN_FLAG
    if _CONN_FLAG is not None:
        return _CONN_FLAG
    probe = run([
        "kubectl", "-n", "cockroachdb", "--request-timeout=15s", "exec",
        "crdb-cluster-0", "--", "ls", "/cockroach/cockroach-certs/ca.crt",
    ])
    if probe.returncode == 0:
        _CONN_FLAG = "--certs-dir=/cockroach/cockroach-certs"
    else:
        _CONN_FLAG = "--insecure"
    return _CONN_FLAG


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
        conn_flag(),
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
