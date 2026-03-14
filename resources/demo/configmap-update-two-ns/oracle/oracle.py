#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys


def _read_configmap_value(namespace: str, configmap: str, key: str):
    cmd = [
        "kubectl",
        "-n",
        str(namespace),
        "get",
        "configmap",
        str(configmap),
        "-o",
        "json",
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or f"failed to read configmap {configmap}"
        raise RuntimeError(msg)

    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        raise RuntimeError(f"failed to parse kubectl output: {exc}") from exc

    data = payload.get("data") or {}
    return str(data.get(str(key), ""))


def _check_value(args) -> int:
    actual = _read_configmap_value(args.namespace, args.configmap, args.key)
    expected = str(args.expected_value)
    role = f" role={args.role}" if getattr(args, "role", None) else ""
    if actual != expected:
        print(
            f"oracle mismatch{role} namespace={args.namespace} configmap={args.configmap} "
            f"key={args.key} expected={expected!r} actual={actual!r}"
        )
        return 1

    print(
        f"oracle ok{role} namespace={args.namespace} configmap={args.configmap} "
        f"key={args.key} value={expected!r}"
    )
    return 0


def _check_distinct(args) -> int:
    source_ns = str(args.source_namespace)
    target_ns = str(args.target_namespace)
    if not source_ns or not target_ns:
        print("oracle invalid args: source and target namespaces are required")
        return 1
    if source_ns == target_ns:
        print(
            "oracle binding mismatch: source and target namespaces are identical "
            f"({source_ns!r})"
        )
        return 1
    print(f"oracle ok source_namespace={source_ns} target_namespace={target_ns} distinct=true")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="subcommand")

    value = sub.add_parser("check-value", help="Check configmap key value in one namespace.")
    value.add_argument("--configmap", default="demo-config")
    value.add_argument("--key", default="value")
    value.add_argument("--expected-value", required=True)
    value.add_argument("--namespace", required=True)
    value.add_argument("--role", choices=["source", "target"])

    distinct = sub.add_parser("check-distinct", help="Check source/target namespace bindings are distinct.")
    distinct.add_argument("--source-namespace", required=True)
    distinct.add_argument("--target-namespace", required=True)

    # Backward-compatible legacy mode (single-namespace value check) when no subcommand is provided.
    parser.add_argument("--configmap", default="demo-config")
    parser.add_argument("--key", default="value")
    parser.add_argument("--expected-value")
    parser.add_argument("--namespace", default=os.environ.get("BENCH_NAMESPACE", "default"))
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        if args.subcommand == "check-distinct":
            return _check_distinct(args)
        if args.subcommand == "check-value":
            return _check_value(args)

        # Legacy fallback.
        if not args.expected_value:
            parser.error("legacy mode requires --expected-value")
        return _check_value(args)
    except RuntimeError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
