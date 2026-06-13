#!/usr/bin/env python3
import subprocess
import sys


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main():
    errors = []

    insecure_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "crdb-cluster-0",
        "--",
        "./cockroach",
        "sql",
        "--insecure",
        "-e",
        "SELECT 1;",
    ]
    insecure_result = run(insecure_cmd)
    if insecure_result.returncode == 0:
        errors.append("Insecure SQL connection unexpectedly succeeded")

    secure_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "crdb-cluster-0",
        "--",
        "./cockroach",
        "sql",
        "--certs-dir=/cockroach/cockroach-certs",
        "-e",
        "SELECT 1;",
    ]
    secure_result = run(secure_cmd)
    if secure_result.returncode != 0:
        errors.append("Secure SQL connection failed")
        errors.append(f"Error: {secure_result.stderr.strip()}")

    if errors:
        print("Certificate generation verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Certificates generated successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
