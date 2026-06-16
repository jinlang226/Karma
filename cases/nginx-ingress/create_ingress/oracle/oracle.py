#!/usr/bin/env python3
import os
import subprocess
import sys

# Param-aware: a workflow can override host/path/expected_body via
# param_overrides; read BENCH_PARAM_* (default = the standalone value) so the
# oracle checks whatever route this stage was asked to create on the live,
# accumulated cluster rather than a baked-in default. Pass criterion unchanged.
HOST = os.environ.get("BENCH_PARAM_HOST") or "demo.example.com"
PATH = os.environ.get("BENCH_PARAM_PATH") or "/app"
EXPECTED_BODY = os.environ.get("BENCH_PARAM_EXPECTED_BODY") or "hello"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main():
    cmd = [
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
    result = run(cmd)
    if result.returncode != 0:
        print("Ingress request failed", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return 1

    body = result.stdout.strip()
    if body == EXPECTED_BODY:
        return 0

    print(f"Unexpected response body: {body}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
