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
        head_ready = ready_replicas("ray-head")
    except Exception as exc:
        print(f"Failed to read ray-head deployment: {exc}", file=sys.stderr)
        return 1
    if head_ready < 1:
        print("ray-head deployment is not ready", file=sys.stderr)
        return 1

    try:
        worker_ready = ready_replicas("ray-worker")
    except Exception as exc:
        print(f"Failed to read ray-worker deployment: {exc}", file=sys.stderr)
        return 1
    if worker_ready < 2:
        print(
            f"ray-worker ready replicas is {worker_ready}, expected 2", file=sys.stderr
        )
        return 1

    try:
        kubectl_json(["-n", "ray", "get", "svc", "ray-head"])
    except Exception as exc:
        print(f"ray-head Service is missing: {exc}", file=sys.stderr)
        return 1

    start_ray_cmd = [
        "kubectl",
        "-n",
        "ray",
        "exec",
        "ray-client",
        "--",
        "ray",
        "start",
        "--address=ray-head:6379",
    ]
    result = run(start_ray_cmd)
    if result.returncode != 0:
        print("Failed to start ray client", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
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
        "import ray; ray.init(address='ray-head:6379'); print('ok'); ray.shutdown()",
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("Ray client check failed", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return 1
    if "ok" not in result.stdout:
        print(f"Unexpected ray-client output: {result.stdout.strip()}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
