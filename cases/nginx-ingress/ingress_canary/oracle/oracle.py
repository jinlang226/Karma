#!/usr/bin/env python3
import os
import subprocess
import sys

# Param-aware: a workflow can override host/header/body values via
# param_overrides; read BENCH_PARAM_* (default = the standalone value) so the
# oracle verifies the header-based routing this stage configured on the live,
# accumulated cluster. Pass criterion (stable without header, canary with
# header) is unchanged.
HOST = os.environ.get("BENCH_PARAM_HOST") or "canary.example.com"
HEADER_NAME = os.environ.get("BENCH_PARAM_HEADER_NAME") or "X-Canary"
HEADER_VALUE = os.environ.get("BENCH_PARAM_HEADER_VALUE") or "always"
STABLE_BODY = os.environ.get("BENCH_PARAM_STABLE_BODY") or "stable"
CANARY_BODY = os.environ.get("BENCH_PARAM_CANARY_BODY") or "canary"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def require_body(label, cmd, expected):
    result = run(cmd)
    if result.returncode != 0:
        print(f"{label} request failed", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return False
    body = result.stdout.strip()
    if body != expected:
        print(f"{label} unexpected body: {body}", file=sys.stderr)
        return False
    return True


def main():
    base = [
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
    ]

    checks = [
        (
            "stable root",
            base + ["http://ingress-nginx-controller.ingress-nginx.svc/"],
            STABLE_BODY,
        ),
        (
            "canary root",
            base
            + [
                "-H",
                f"{HEADER_NAME}: {HEADER_VALUE}",
                "http://ingress-nginx-controller.ingress-nginx.svc/",
            ],
            CANARY_BODY,
        ),
    ]

    ok = True
    for label, cmd, expected in checks:
        if not require_body(label, cmd, expected):
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
