#!/usr/bin/env python3
import base64
import json
import os
import subprocess
import time
from pathlib import Path


def _normalize_kubeconfig():
    raw = (os.environ.get("KUBECONFIG") or "").strip()
    submit_file = (os.environ.get("BENCHMARK_SUBMIT_FILE") or "").strip()
    workspace_root = Path(submit_file).resolve().parent if submit_file else None
    if raw:
        if os.path.isabs(raw):
            return
        direct = Path(raw)
        if direct.exists():
            os.environ["KUBECONFIG"] = str(direct.resolve())
            return
        if workspace_root is not None:
            candidate = (workspace_root / raw).resolve()
            if candidate.exists():
                os.environ["KUBECONFIG"] = str(candidate)
                return
    if workspace_root is not None:
        default_candidate = (workspace_root / "kubeconfig-proxy").resolve()
        if default_candidate.exists():
            os.environ["KUBECONFIG"] = str(default_candidate)


def _subprocess_env():
    _normalize_kubeconfig()
    return os.environ.copy()


_normalize_kubeconfig()


def run(cmd, input_text=None):
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        env=_subprocess_env(),
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
