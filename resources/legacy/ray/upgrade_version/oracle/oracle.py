#!/usr/bin/env python3
import json
import subprocess
import sys


TARGET_IMAGE = "rayproject/ray:2.9.0"


def run(cmd):
    return subprocess.run(
        cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )


def kubectl_json(args):
    result = run(["kubectl"] + args + ["-o", "json"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "kubectl command failed")
    return json.loads(result.stdout)


def deployment_image(name):
    data = kubectl_json(["-n", "ray", "get", "deploy", name])
    return data["spec"]["template"]["spec"]["containers"][0]["image"]


def ready_replicas(name):
    data = kubectl_json(["-n", "ray", "get", "deploy", name])
    return data.get("status", {}).get("readyReplicas", 0)


def main():
    try:
        head_image = deployment_image("ray-head")
        worker_image = deployment_image("ray-worker")
    except Exception as exc:
        print(f"Failed to read deployment images: {exc}", file=sys.stderr)
        return 1

    if head_image != TARGET_IMAGE:
        print(
            f"ray-head image is {head_image}, expected {TARGET_IMAGE}", file=sys.stderr
        )
        return 1
    if worker_image != TARGET_IMAGE:
        print(
            f"ray-worker image is {worker_image}, expected {TARGET_IMAGE}",
            file=sys.stderr,
        )
        return 1

    if ready_replicas("ray-head") < 1:
        print("ray-head deployment is not ready", file=sys.stderr)
        return 1
    if ready_replicas("ray-worker") < 2:
        print("ray-worker deployment is not ready", file=sys.stderr)
        return 1

    # kubectl -n ray set image pod/ray-client ray-client=rayproject/ray:2.9.0
    set_image = run(
        [
            "kubectl",
            "-n",
            "ray",
            "set",
            "image",
            "pod/ray-client",
            f"ray-client={TARGET_IMAGE}",
        ]
    )
    if set_image.returncode != 0:
        print(
            f"Failed to set ray-client image: {set_image.stderr.strip()}",
            file=sys.stderr,
        )
        return 1
    # kubectl -n ray wait --for=condition=Ready pod/ray-client --timeout=60s
    wait_ready = run(
        [
            "kubectl",
            "-n",
            "ray",
            "wait",
            "--for=condition=Ready",
            "pod/ray-client",
            "--timeout=60s",
        ]
    )
    if wait_ready.returncode != 0:
        print(
            f"ray-client pod did not become ready: {wait_ready.stderr.strip()}",
            file=sys.stderr,
        )
        return 1
    # kubectl -n ray exec ray-client -- ray start --address=ray-head:6379
    start_ray = run(
        [
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
    )
    if start_ray.returncode != 0:
        print(
            f"Failed to start ray on ray-client: {start_ray.stderr.strip()}",
            file=sys.stderr,
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
        "import ray; ray.init(address='ray-head:6379'); print('ok'); ray.shutdown()",
    ]
    result = run(cmd)
    if result.returncode != 0:
        print("Ray client check failed after upgrade", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
        return 1
    if "ok" not in result.stdout:
        print(f"Unexpected ray-client output: {result.stdout.strip()}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
