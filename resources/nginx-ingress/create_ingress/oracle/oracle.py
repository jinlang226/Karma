#!/usr/bin/env python3
import subprocess
import sys


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
        "Host: demo.example.com",
        "http://ingress-nginx-controller.ingress-nginx.svc/app",
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("Ingress request failed", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return 1

    body = result.stdout.strip()
    if body == "hello":
        return 0

    print(f"Unexpected response body: {body}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
