#!/usr/bin/env python3
import json
import subprocess
import sys


def run(cmd):
    return subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def kubectl_json(args):
    result = run(["kubectl"] + args + ["-o", "json"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "kubectl command failed")
    return json.loads(result.stdout)


def ready_replicas(name):
    data = kubectl_json(["-n", "ray", "get", "deploy", name])
    return data.get("status", {}).get("readyReplicas", 0)


def main():
    try:
        worker_ready = ready_replicas("ray-worker")
    except Exception as exc:
        print(f"Failed to read ray-worker deployment: {exc}", file=sys.stderr)
        return 1

    if worker_ready < 3:
        print(
            f"ray-worker ready replicas is {worker_ready}, expected 3", file=sys.stderr
        )
        return 1

    cmd = [
        "kubectl",
        "-n",
        "ray",
        "exec",
        "ray-client",
        "--",
        "python",
        "-c",
        "import ray; ray.init(address='ray-head:6379'); alive=[n for n in ray.nodes() if n.get('Alive')]; print(len(alive)); ray.shutdown()",
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("Ray node check failed", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return 1

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        print("Ray node check produced no output", file=sys.stderr)
        return 1

    try:
        node_count = int(lines[-1])
    except ValueError:
        print(f"Unexpected node count output: {lines[-1]}", file=sys.stderr)
        return 1

    if node_count < 4:
        print(f"Ray reports {node_count} nodes, expected at least 4", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
