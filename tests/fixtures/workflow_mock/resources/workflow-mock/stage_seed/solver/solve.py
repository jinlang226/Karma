#!/usr/bin/env python3
import argparse
import os
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", default="alpha")
    parser.add_argument("--namespace", default=os.environ.get("BENCH_NAMESPACE", "workflow-mock"))
    args = parser.parse_args()

    patch = "{\"data\":{\"phase\":\"" + str(args.phase) + "\"}}"
    subprocess.check_call(
        [
            "kubectl",
            "-n",
            str(args.namespace),
            "patch",
            "configmap",
            "wf-state",
            "--type",
            "merge",
            "-p",
            patch,
        ]
    )
    print(f"patched wf-state phase={args.phase} namespace={args.namespace}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
