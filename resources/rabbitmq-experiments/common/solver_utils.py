#!/usr/bin/env python3
import base64
import json
import subprocess
import time


def run(cmd, input_text=None):
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        detail = stderr or stdout or f"exit={proc.returncode}"
        raise RuntimeError(f"command failed: {' '.join(str(c) for c in cmd)} :: {detail}")
    return proc.stdout


def run_json(cmd):
    out = run(cmd)
    return json.loads(out or "{}")


def kubectl(*args):
    return run(["kubectl", *args])


def kubectl_json(*args):
    return run_json(["kubectl", *args, "-o", "json"])


def get_secret_value(namespace, name, key):
    payload = kubectl_json("-n", namespace, "get", "secret", name)
    raw = ((payload.get("data") or {}).get(key) or "").strip()
    if not raw:
        raise RuntimeError(f"missing secret key: {name}/{key}")
    return base64.b64decode(raw).decode().strip()


def wait_until(predicate, timeout_sec=120, interval_sec=2, description="condition"):
    end = time.time() + timeout_sec
    last_error = None
    while time.time() < end:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(interval_sec)
    if last_error:
        raise RuntimeError(f"timeout waiting for {description}: {last_error}")
    raise RuntimeError(f"timeout waiting for {description}")


def wait_deployment_ready(namespace, name, timeout_sec=300):
    kubectl("-n", namespace, "rollout", "status", f"deployment/{name}", f"--timeout={timeout_sec}s")


def wait_statefulset_ready(namespace, name, timeout_sec=900):
    kubectl("-n", namespace, "rollout", "status", f"statefulset/{name}", f"--timeout={timeout_sec}s")


def wait_pods_ready(namespace, label_selector, timeout_sec=600):
    kubectl(
        "-n",
        namespace,
        "wait",
        "--for=condition=ready",
        "pod",
        "-l",
        label_selector,
        f"--timeout={timeout_sec}s",
    )


def apply_yaml(yaml_text, namespace=None):
    cmd = ["kubectl"]
    if namespace:
        cmd.extend(["-n", namespace])
    cmd.extend(["apply", "-f", "-"])
    run(cmd, input_text=yaml_text)
