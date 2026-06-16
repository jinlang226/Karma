#!/usr/bin/env python3
import os
import subprocess
import sys


def load_ingress_env(path="/tmp/ingress_env"):
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :]
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def derive_node_ip():
    # Discover a node InternalIP from the live cluster so the oracle does not
    # depend on /tmp/ingress_env (which a chained workflow may never write
    # because the env-ready precondition's destructive apply was skipped to
    # preserve accumulated state).
    cmd = [
        "kubectl",
        "get",
        "nodes",
        "-o",
        "jsonpath={.items[*].status.addresses[?(@.type==\"InternalIP\")].address}",
    ]
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return None
    # jsonpath over all nodes returns a space-separated list; take the first.
    addresses = result.stdout.split()
    return addresses[0] if addresses else None


def derive_https_node_port():
    # Discover the ingress-nginx controller Service's https (443) nodePort from
    # the live cluster, mirroring how the precondition computes INGRESS_HTTPS_PORT.
    cmd = [
        "kubectl",
        "get",
        "svc",
        "ingress-nginx-controller",
        "-n",
        "ingress-nginx",
        "-o",
        "jsonpath={.spec.ports[?(@.port==443)].nodePort}",
    ]
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return None
    port = result.stdout.strip()
    return port or None


def main():
    # Param-aware: a workflow can override host/expected_body via
    # param_overrides; read BENCH_PARAM_* (default = the standalone value) so
    # the oracle validates HTTPS against whichever host this stage targets on
    # the live cluster. Pass criterion (valid cert, body matches) is unchanged.
    host = os.environ.get("BENCH_PARAM_HOST") or "demo.example.com"
    expected_body = os.environ.get("BENCH_PARAM_EXPECTED_BODY") or "hello"

    # Resolve the ingress endpoint. Prefer the precondition-written
    # /tmp/ingress_env (or env vars) when present, but SELF-DERIVE from the live
    # cluster otherwise so the oracle works whether the case ran standalone or
    # chained behind another stage (where /tmp/ingress_env may be missing).
    env = load_ingress_env()
    node_ip = env.get("INGRESS_NODE_IP") or os.environ.get("INGRESS_NODE_IP")
    node_port = env.get("INGRESS_HTTPS_PORT") or os.environ.get("INGRESS_HTTPS_PORT")
    if not node_ip:
        node_ip = derive_node_ip()
    if not node_port:
        node_port = derive_https_node_port()
    if not node_ip or not node_port:
        print(
            "Could not determine INGRESS_NODE_IP / INGRESS_HTTPS_PORT "
            "(no /tmp/ingress_env and could not derive from the cluster)",
            file=sys.stderr,
        )
        return 1

    resolve = f"{host}:{node_port}:{node_ip}"
    url = f"https://{host}:{node_port}/"

    cmd = [
        "kubectl",
        "-n",
        "demo",
        "exec",
        "curl-test",
        "--",
        "curl",
        "-sS",
        "--cacert",
        "/tmp/tls/ca.crt",
        "--resolve",
        resolve,
        url,
    ]

    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        body = result.stdout.strip()
        if body == expected_body:
            return 0
        print(f"Unexpected body: {body}", file=sys.stderr)
        return 1

    if result.returncode == 60 and "certificate has expired" in result.stderr:
        print("Certificate is still expired", file=sys.stderr)
        return 1

    print(result.stderr.strip(), file=sys.stderr)
    return result.returncode or 1


if __name__ == "__main__":
    sys.exit(main())
