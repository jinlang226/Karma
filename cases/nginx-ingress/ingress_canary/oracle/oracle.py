#!/usr/bin/env python3
import subprocess
import sys


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
        "Host: canary.example.com",
    ]

    checks = [
        (
            "stable root",
            base + ["http://ingress-nginx-controller.ingress-nginx.svc/"],
            "stable",
        ),
        (
            "canary root",
            base
            + [
                "-H",
                "X-Canary: always",
                "http://ingress-nginx-controller.ingress-nginx.svc/",
            ],
            "canary",
        ),
    ]

    ok = True
    for label, cmd, expected in checks:
        if not require_body(label, cmd, expected):
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
