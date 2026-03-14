#!/usr/bin/env python3
import subprocess
import sys


def run(cmd):
    return subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def main():
    cmd = ["kubectl", "-n", "ray", "exec", "ray-client", "--", "python", "/opt/job.py"]
    result = run(cmd)
    if result.returncode != 0:
        print("Job execution failed", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return 1

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        print("Job output was empty", file=sys.stderr)
        return 1

    if lines[-1] != "pong":
        print(f"Unexpected job output: {lines[-1]}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
