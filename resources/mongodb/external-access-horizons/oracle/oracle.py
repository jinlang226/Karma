#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongo-rs")
SERVICE_NAME = os.environ.get("BENCH_PARAM_SERVICE_NAME", "mongo")
REPLICA_SET_NAME = os.environ.get("BENCH_PARAM_REPLICA_SET_NAME", "rs0")
CLIENT_POD_NAME = os.environ.get("BENCH_PARAM_CLIENT_POD_NAME", "mongo-client")
EXTERNAL_HOST_PREFIX = os.environ.get("BENCH_PARAM_EXTERNAL_HOST_PREFIX", "domain-rs")
NODEPORT_START = int(os.environ.get("BENCH_PARAM_NODEPORT_START", "31181"))
EXPECTED_REPLICAS = int(os.environ.get("BENCH_PARAM_EXPECTED_REPLICAS", "3"))


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def fail(prefix, errors):
    if errors:
        print(prefix, file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


def mongo_json(pod, eval_str, label, errors, uri=None):
    cmd = ["kubectl", "-n", NAMESPACE, "exec", pod, "--", "mongosh", "--quiet"]
    if uri:
        cmd.append(uri)
    cmd.extend(["--eval", eval_str])
    res = run(cmd)
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        errors.append(f"{label} failed: {detail}")
        return None
    raw = (res.stdout or "").strip()
    if not raw:
        errors.append(f"{label} returned empty output")
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        errors.append(f"Unable to parse {label} JSON output")
        return None


def check_topology():
    errors = []
    pod = f"{CLUSTER_PREFIX}-0"
    conf = mongo_json(pod, "JSON.stringify(rs.conf())", "rs.conf()", errors)
    if isinstance(conf, dict):
        members = conf.get("members", [])
        if len(members) != EXPECTED_REPLICAS:
            errors.append(f"Expected {EXPECTED_REPLICAS} members in rs.conf(), got {len(members)}")

        expected_hosts = {
            f"{CLUSTER_PREFIX}-{idx}.{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:27017": idx
            for idx in range(EXPECTED_REPLICAS)
        }
        for member in members:
            host = member.get("host")
            if host not in expected_hosts:
                errors.append(f"Unexpected member host: {host}")
                continue
            idx = expected_hosts[host]
            expected_horizon = f"{EXTERNAL_HOST_PREFIX}-{idx + 1}:{NODEPORT_START + idx}"
            horizons = member.get("horizons") or {}
            actual_horizon = horizons.get("horizon1")
            if actual_horizon != expected_horizon:
                errors.append(f"{host} horizon1 expected {expected_horizon}, got {actual_horizon}")

    return fail("External access horizons topology check failed:", errors)


def check_services():
    errors = []
    for idx in range(EXPECTED_REPLICAS):
        svc_name = f"mongo-external-{idx}"
        res = run(["kubectl", "-n", NAMESPACE, "get", "svc", svc_name, "-o", "json"])
        if res.returncode != 0:
            detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
            errors.append(f"Failed to read service/{svc_name}: {detail}")
            continue
        try:
            svc = json.loads(res.stdout)
        except json.JSONDecodeError:
            errors.append(f"Failed to parse service/{svc_name} JSON")
            continue

        ports = svc.get("spec", {}).get("ports", [])
        node_port = ports[0].get("nodePort") if ports else None
        expected_node_port = NODEPORT_START + idx
        if node_port != expected_node_port:
            errors.append(f"{svc_name} nodePort expected {expected_node_port}, got {node_port}")

        selector = svc.get("spec", {}).get("selector", {})
        expected_selector = {"statefulset.kubernetes.io/pod-name": f"{CLUSTER_PREFIX}-{idx}"}
        if selector != expected_selector:
            errors.append(f"{svc_name} selector mismatch: expected={expected_selector} actual={selector}")

    return fail("External access horizons service check failed:", errors)


def check_connectivity():
    errors = []
    hosts = ",".join(
        f"{EXTERNAL_HOST_PREFIX}-{idx + 1}:{NODEPORT_START + idx}" for idx in range(EXPECTED_REPLICAS)
    )
    uri = f"mongodb://{hosts}/admin?replicaSet={REPLICA_SET_NAME}"

    res = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            CLIENT_POD_NAME,
            "--",
            "mongosh",
            "--quiet",
            uri,
            "--eval",
            "JSON.stringify(db.hello())",
        ]
    )
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        errors.append(f"mongo-client connectivity failed: {detail}")
        return fail("External access horizons connectivity check failed:", errors)

    try:
        hello = json.loads((res.stdout or "").strip())
    except json.JSONDecodeError:
        errors.append("Unable to parse mongo-client db.hello() output")
        return fail("External access horizons connectivity check failed:", errors)

    if hello.get("ok") != 1:
        errors.append("db.hello().ok != 1")
    if hello.get("setName") != REPLICA_SET_NAME:
        errors.append(f"Connected replica set expected {REPLICA_SET_NAME}, got {hello.get('setName')}")

    return fail("External access horizons connectivity check failed:", errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", default="all", choices=["all", "topology", "services", "connectivity"])
    args = parser.parse_args()

    if args.check == "topology":
        return check_topology()
    if args.check == "services":
        return check_services()
    if args.check == "connectivity":
        return check_connectivity()

    for fn in (check_topology, check_services, check_connectivity):
        rc = fn()
        if rc != 0:
            return rc
    print("External access horizons verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
