#!/usr/bin/env python3
import subprocess
import sys


def run(cmd):
    return subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def ensure_missing(kind, name):
    result = run(["kubectl", "-n", "ray", "get", kind, name])
    if result.returncode == 0:
        print(f"{kind}/{name} still exists", file=sys.stderr)
        return False
    err = result.stderr.lower()
    if "notfound" in err or "not found" in err:
        return True
    print(
        f"Unexpected error checking {kind}/{name}: {result.stderr.strip()}",
        file=sys.stderr,
    )
    return False


def main():
    ns_result = run(["kubectl", "get", "ns", "ray"])
    if ns_result.returncode != 0:
        return 0

    checks = [
        ("deploy", "ray-head"),
        ("deploy", "ray-worker"),
        ("svc", "ray-head"),
    ]

    ok = True
    for kind, name in checks:
        if not ensure_missing(kind, name):
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
