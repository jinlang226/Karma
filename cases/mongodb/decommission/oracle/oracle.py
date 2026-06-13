#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongo-rs")
SERVICE_NAME = os.environ.get("BENCH_PARAM_SERVICE_NAME", "mongo")
TARGET_REPLICAS = int(os.environ.get("BENCH_PARAM_TARGET_REPLICAS", "2"))
REMOVED_MEMBER_INDEX = int(os.environ.get("BENCH_PARAM_REMOVED_MEMBER_INDEX", "2"))
APP_DATABASE = os.environ.get("BENCH_PARAM_APP_DATABASE", "app")
APP_COLLECTION = os.environ.get("BENCH_PARAM_APP_COLLECTION", "test")
SEED_DOCS = int(os.environ.get("BENCH_PARAM_SEED_DOCS", "3"))


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def fail(prefix, errors):
    if errors:
        print(prefix, file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


def mongo_eval(pod, script):
    return run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            pod,
            "--",
            "mongosh",
            "--quiet",
            "--eval",
            script,
        ]
    )


def mongo_json(pod, script, label, errors):
    res = mongo_eval(pod, script)
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        errors.append(f"{label} failed on {pod}: {detail}")
        return None
    raw = (res.stdout or "").strip()
    if not raw:
        errors.append(f"{label} returned empty output on {pod}")
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        errors.append(f"Unable to parse {label} JSON output on {pod}")
        return None


def check_workload():
    errors = []
    res = run(["kubectl", "-n", NAMESPACE, "get", "sts", CLUSTER_PREFIX, "-o", "json"])
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        errors.append(f"Failed to read statefulset {CLUSTER_PREFIX}: {detail}")
        return fail("MongoDB decommission workload check failed:", errors)
    try:
        sts = json.loads(res.stdout)
    except json.JSONDecodeError:
        errors.append("Failed to parse statefulset JSON")
        return fail("MongoDB decommission workload check failed:", errors)

    spec_replicas = sts.get("spec", {}).get("replicas")
    ready_replicas = sts.get("status", {}).get("readyReplicas", 0)
    if spec_replicas != TARGET_REPLICAS:
        errors.append(f"StatefulSet replicas expected {TARGET_REPLICAS}, got {spec_replicas}")
    if ready_replicas != TARGET_REPLICAS:
        errors.append(f"Ready replicas expected {TARGET_REPLICAS}, got {ready_replicas}")

    return fail("MongoDB decommission workload check failed:", errors)


def check_topology():
    errors = []
    pod = f"{CLUSTER_PREFIX}-0"

    conf = mongo_json(pod, "JSON.stringify(rs.conf())", "rs.conf()", errors)
    if isinstance(conf, dict):
        members = conf.get("members", [])
        if len(members) != TARGET_REPLICAS:
            errors.append(f"Expected {TARGET_REPLICAS} members in rs.conf(), got {len(members)}")
        hosts = {m.get("host") for m in members if m.get("host")}
        expected_hosts = {
            f"{CLUSTER_PREFIX}-{idx}.{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:27017"
            for idx in range(TARGET_REPLICAS)
        }
        if hosts != expected_hosts:
            errors.append(f"Replica set hosts mismatch: expected={sorted(expected_hosts)} actual={sorted(hosts)}")
        removed_host = f"{CLUSTER_PREFIX}-{REMOVED_MEMBER_INDEX}.{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:27017"
        if removed_host in hosts:
            errors.append(f"Removed member still present: {removed_host}")

    status = mongo_json(pod, "JSON.stringify(rs.status())", "rs.status()", errors)
    if isinstance(status, dict):
        members = status.get("members", [])
        p = sum(1 for m in members if m.get("stateStr") == "PRIMARY")
        s = sum(1 for m in members if m.get("stateStr") == "SECONDARY")
        if p != 1:
            errors.append(f"Expected 1 PRIMARY, got {p}")
        if s != TARGET_REPLICAS - 1:
            errors.append(f"Expected {TARGET_REPLICAS - 1} SECONDARY, got {s}")

    return fail("MongoDB decommission topology check failed:", errors)


def check_data():
    errors = []
    pod = f"{CLUSTER_PREFIX}-0"
    res = mongo_eval(
        pod,
        f"db.getSiblingDB('{APP_DATABASE}').{APP_COLLECTION}.countDocuments({{}})",
    )
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        errors.append(f"Failed to read {APP_DATABASE}.{APP_COLLECTION}: {detail}")
    else:
        raw = (res.stdout or "").strip()
        if not raw.isdigit() or int(raw) < SEED_DOCS:
            errors.append(f"Expected >= {SEED_DOCS} docs in {APP_DATABASE}.{APP_COLLECTION}, got {raw}")

    return fail("MongoDB decommission data check failed:", errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", default="all", choices=["all", "workload", "topology", "data"])
    args = parser.parse_args()

    if args.check == "workload":
        return check_workload()
    if args.check == "topology":
        return check_topology()
    if args.check == "data":
        return check_data()

    for fn in (check_workload, check_topology, check_data):
        rc = fn()
        if rc != 0:
            return rc
    print("MongoDB decommission verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
