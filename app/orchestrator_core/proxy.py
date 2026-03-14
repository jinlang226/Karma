from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .common import normalize_control_url


def proxy_control_running(control_url, timeout=2.0):
    if not control_url:
        return False
    normalized = normalize_control_url(control_url)
    url = f"{normalized.rstrip('/')}/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def start_local_proxy(repo_root, listen, control_listen, upstream, log_path):
    proxy_path = Path(repo_root) / "proxy.py"
    if not proxy_path.exists():
        raise RuntimeError("proxy.py not found for local proxy.")
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8")
    cmd = [
        sys.executable,
        str(proxy_path),
        "--listen",
        listen,
        "--upstream",
        upstream,
        "--control-listen",
        control_listen,
    ]
    return subprocess.Popen(cmd, stdout=log_handle, stderr=log_handle)


def wait_for_proxy(control_url, timeout=5.0, poll=0.2, request_timeout=2.0):
    start = time.time()
    while time.time() - start < timeout:
        if proxy_control_running(control_url, timeout=request_timeout):
            return True
        time.sleep(poll)
    return False


def resolve_api_server(source_kubeconfig, environ=None):
    env = dict(environ) if environ is not None else os.environ.copy()
    if source_kubeconfig:
        env["KUBECONFIG"] = source_kubeconfig
    output = subprocess.check_output(
        ["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"],
        text=True,
        env=env,
    ).strip()
    if not output:
        raise RuntimeError("Failed to resolve API server from kubeconfig.")
    output = output.replace("https://", "").replace("http://", "")
    return output.split("/", 1)[0]


def normalize_upstream_host(upstream):
    host, _, port = upstream.partition(":")
    if host in ("127.0.0.1", "localhost"):
        host = "host.docker.internal"
    if port:
        return f"{host}:{port}"
    return host


def docker_network_create(name):
    subprocess.check_call(["docker", "network", "create", name])


def docker_network_remove(name):
    subprocess.run(["docker", "network", "rm", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def docker_start_proxy_container(repo_root, runs_dir, network_name, proxy_name, upstream, control_port):
    proxy_path = Path(repo_root) / "proxy.py"
    if not proxy_path.exists():
        raise RuntimeError("proxy.py not found for proxy container.")
    runs_dir = Path(runs_dir)
    cmd = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        proxy_name,
        "--network",
        network_name,
        "-v",
        f"{proxy_path}:{'/proxy.py'}:ro",
        "-v",
        f"{runs_dir}:{runs_dir}",
        "-p",
        f"{control_port}:8082",
        "python:3.11-slim",
        "python",
        "/proxy.py",
        "--listen",
        "0.0.0.0:8081",
        "--upstream",
        upstream,
        "--control-listen",
        "0.0.0.0:8082",
    ]
    subprocess.check_call(cmd)


def docker_container_ip(name):
    output = subprocess.check_output(
        ["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name],
        text=True,
    ).strip()
    if not output:
        raise RuntimeError("Failed to resolve proxy container IP.")
    return output


def docker_stop_container(name):
    subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def docker_build_image(tag, dockerfile, context_dir):
    cmd = [
        "docker",
        "build",
        "-t",
        tag,
        "-f",
        str(dockerfile),
        str(context_dir),
    ]
    subprocess.check_call(cmd)
