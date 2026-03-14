#!/usr/bin/env python3
import subprocess
import sys


def run(cmd):
    return subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def main():
    cmd = [
        "kubectl",
        "-n",
        "ray",
        "exec",
        "curl-test",
        "--",
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "http://ray-head:8265/api/cluster_status",
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("Dashboard curl request failed", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return 1

    code = result.stdout.strip()
    if code != "200":
        print(f"Unexpected dashboard HTTP status: {code}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
