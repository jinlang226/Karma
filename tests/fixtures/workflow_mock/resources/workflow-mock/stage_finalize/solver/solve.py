#!/usr/bin/env python3
import argparse
import os
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", default="omega")
    parser.add_argument("--replicas", default="5")
    parser.add_argument("--migration", default="done")
    parser.add_argument("--namespace", default=os.environ.get("BENCH_NAMESPACE", "workflow-mock"))
    args = parser.parse_args()

    patch = (
        "{\"data\":{\"phase\":\""
        + str(args.phase)
        + "\",\"replicas\":\""
        + str(args.replicas)
        + "\",\"migration\":\""
        + str(args.migration)
        + "\"}}"
    )
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
    print(
        f"patched wf-state final migration state phase={args.phase} replicas={args.replicas} "
        f"migration={args.migration} namespace={args.namespace}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
