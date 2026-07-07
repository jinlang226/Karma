#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import sys
import time
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    configmap_value,
    configmap,
    deployment_ready_replicas,
    job_logs,
    job_succeeded,
    names_from_env,
    ray_node_count_from_head,
    service_ports,
)

CONNECTIVITY_TOTAL_TIMEOUT_SEC = 60
CONNECTIVITY_ATTEMPT_TIMEOUT_SEC = 12


def job_payload(logs: str) -> dict:
    lines = [line.strip() for line in logs.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("job logs did not contain a JSON payload")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["cluster", "connectivity", "script", "job-runner", "result"])
    parser.add_argument("--expected-workers", type=int, required=True)
    parser.add_argument("--expected-output", required=True)
    args = parser.parse_args()

    ns = bench_namespace()
    names = names_from_env()

    if args.check == "cluster":
        ports = service_ports(ns, names.head)
        if 6379 not in ports:
            print(f"service/{names.head} does not expose port 6379")
            return 1
        head_ready = deployment_ready_replicas(ns, names.head)
        if head_ready < 1:
            print(f"deployment/{names.head} ready replicas {head_ready}, expected at least 1")
            return 1
        worker_ready = deployment_ready_replicas(ns, names.worker)
        if worker_ready < args.expected_workers:
            print(
                f"deployment/{names.worker} ready replicas {worker_ready}, expected at least {args.expected_workers}"
            )
            return 1
        print("ray cluster baseline is healthy")
        return 0

    if args.check == "connectivity":
        expected_nodes = 1 + args.expected_workers
        deadline = time.time() + CONNECTIVITY_TOTAL_TIMEOUT_SEC
        last_count = 0
        last_error = ""
        while time.time() < deadline:
            try:
                node_count = ray_node_count_from_head(
                    ns,
                    names.head,
                    timeout_sec=CONNECTIVITY_ATTEMPT_TIMEOUT_SEC,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                time.sleep(3)
                continue
            last_count = node_count
            if node_count >= expected_nodes:
                print(f"ray reports {node_count} nodes")
                return 0
            time.sleep(3)
        if last_error:
            print(f"ray connectivity probe failed: {last_error}")
            return 1
        print(f"ray reports {last_count} nodes, expected at least {expected_nodes}")
        return 1

    if args.check == "script":
        script = str((configmap(ns, names.job_script).get("data", {}) or {}).get("job.py") or "")
        if not script:
            print(f"configmap/{names.job_script} is missing key 'job.py'")
            return 1
        required_snippets = [
            'ray.init(',
            'address="auto"',
            "_node_ip_address=os.environ.get",
            "socket.gethostname()",
            '"worker_hostnames"',
            "json.dumps(payload, sort_keys=True)",
        ]
        missing = [snippet for snippet in required_snippets if snippet not in script]
        if missing:
            print(f"configmap/{names.job_script} job.py is missing expected content: {missing!r}")
            return 1
        try:
            tree = ast.parse(script)
        except SyntaxError as exc:
            print(f"configmap/{names.job_script} job.py is invalid Python: {exc}")
            return 1
        constants = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if target.id not in {"EXPECTED_WORKERS", "EXPECTED_OUTPUT"}:
                continue
            try:
                constants[target.id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
        if constants.get("EXPECTED_WORKERS") != args.expected_workers:
            print(
                f"configmap/{names.job_script} EXPECTED_WORKERS="
                f"{constants.get('EXPECTED_WORKERS')!r}, expected {args.expected_workers}"
            )
            return 1
        if constants.get("EXPECTED_OUTPUT") != args.expected_output:
            print(
                f"configmap/{names.job_script} EXPECTED_OUTPUT="
                f"{constants.get('EXPECTED_OUTPUT')!r}, expected {args.expected_output!r}"
            )
            return 1
        print(f"configmap/{names.job_script} retains the expected script content")
        return 0

    if args.check == "job-runner":
        if not job_succeeded(ns, names.job_runner):
            print(f"job/{names.job_runner} did not complete successfully")
            return 1
        logs = job_logs(ns, names.job_runner)
        try:
            payload = job_payload(logs)
        except ValueError as exc:
            print(f"job/{names.job_runner} {exc}")
            return 1
        message = str(payload.get("message") or "")
        if message != args.expected_output:
            print(f"job/{names.job_runner} message {message!r}, expected {args.expected_output!r}")
            return 1
        head_hostname = str(payload.get("head_hostname") or "").strip()
        worker_hostnames = sorted(
            {
                str(host).strip()
                for host in (payload.get("worker_hostnames") or [])
                if str(host).strip()
            }
        )
        if len(worker_hostnames) < args.expected_workers:
            print(
                f"job/{names.job_runner} reached workers {worker_hostnames!r}, "
                f"expected at least {args.expected_workers} distinct worker hosts"
            )
            return 1
        if head_hostname and head_hostname in worker_hostnames:
            print(f"job/{names.job_runner} scheduled work on head host {head_hostname!r}")
            return 1
        try:
            node_count = int(payload.get("node_count", 0) or 0)
        except (TypeError, ValueError):
            node_count = 0
        expected_nodes = 1 + args.expected_workers
        if node_count < expected_nodes:
            print(f"job/{names.job_runner} observed {node_count} nodes, expected at least {expected_nodes}")
            return 1
        print(
            f"job/{names.job_runner} completed with expected output {args.expected_output!r} "
            f"with visible workers {worker_hostnames!r}"
        )
        return 0

    result = configmap_value(ns, f"{names.cluster_prefix}-job-result", "result")
    if not result:
        print(f"configmap/{names.cluster_prefix}-job-result is missing key 'result'")
        return 1
    if result != args.expected_output:
        print(
            f"configmap/{names.cluster_prefix}-job-result result {result!r}, expected {args.expected_output!r}"
        )
        return 1
    print(f"configmap/{names.cluster_prefix}-job-result recorded {result!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
