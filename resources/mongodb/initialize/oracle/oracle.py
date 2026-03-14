#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongodb-replica")
HEADLESS_SERVICE = os.environ.get("BENCH_PARAM_HEADLESS_SERVICE_NAME", "mongodb-replica-svc")
REPLICA_SET_NAME = os.environ.get("BENCH_PARAM_REPLICA_SET_NAME", CLUSTER_PREFIX)
EXPECTED_REPLICAS = int(os.environ.get("BENCH_PARAM_EXPECTED_REPLICAS", "3"))
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


def expected_hosts():
    return {
        f"{CLUSTER_PREFIX}-{i}.{HEADLESS_SERVICE}.{NAMESPACE}.svc.cluster.local:27017"
        for i in range(EXPECTED_REPLICAS)
    }


def check_topology():
    errors = []
    pod = f"{CLUSTER_PREFIX}-0"
    conf = mongo_json(pod, "JSON.stringify(rs.conf())", "rs.conf()", errors)
    if isinstance(conf, dict):
        members = conf.get("members", [])
        if len(members) != EXPECTED_REPLICAS:
            errors.append(f"Expected {EXPECTED_REPLICAS} members in rs.conf(), got {len(members)}")
        actual_hosts = {m.get("host") for m in members if m.get("host")}
        want_hosts = expected_hosts()
        if actual_hosts != want_hosts:
            errors.append(f"Replica set hosts mismatch: expected={sorted(want_hosts)} actual={sorted(actual_hosts)}")

    status = mongo_json(pod, "JSON.stringify(rs.status())", "rs.status()", errors)
    if isinstance(status, dict):
        set_name = status.get("set")
        if set_name != REPLICA_SET_NAME:
            errors.append(f"Replica set name expected {REPLICA_SET_NAME}, got {set_name}")

    return fail("MongoDB initialize topology check failed:", errors)


def check_health():
    errors = []
    pod = f"{CLUSTER_PREFIX}-0"
    status = mongo_json(pod, "JSON.stringify(rs.status())", "rs.status()", errors)
    if isinstance(status, dict):
        members = status.get("members", [])
        primary = sum(1 for m in members if m.get("stateStr") == "PRIMARY")
        secondary = sum(1 for m in members if m.get("stateStr") == "SECONDARY")
        if primary != 1:
            errors.append(f"Expected 1 PRIMARY, got {primary}")
        if secondary != EXPECTED_REPLICAS - 1:
            errors.append(f"Expected {EXPECTED_REPLICAS - 1} SECONDARY, got {secondary}")

    sts = run(["kubectl", "-n", NAMESPACE, "get", "sts", CLUSTER_PREFIX, "-o", "json"])
    if sts.returncode != 0:
        detail = sts.stderr.strip() or sts.stdout.strip() or f"exit {sts.returncode}"
        errors.append(f"Failed to read statefulset {CLUSTER_PREFIX}: {detail}")
    else:
        try:
            sts_obj = json.loads(sts.stdout)
        except json.JSONDecodeError:
            errors.append("Failed to parse statefulset JSON")
            sts_obj = {}
        spec_replicas = sts_obj.get("spec", {}).get("replicas")
        ready_replicas = sts_obj.get("status", {}).get("readyReplicas", 0)
        if spec_replicas != EXPECTED_REPLICAS:
            errors.append(f"StatefulSet replicas expected {EXPECTED_REPLICAS}, got {spec_replicas}")
        if ready_replicas != EXPECTED_REPLICAS:
            errors.append(f"Ready replicas expected {EXPECTED_REPLICAS}, got {ready_replicas}")

    return fail("MongoDB initialize health check failed:", errors)


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

    return fail("MongoDB initialize data check failed:", errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", default="all", choices=["all", "topology", "health", "data"])
    args = parser.parse_args()

    if args.check == "topology":
        return check_topology()
    if args.check == "health":
        return check_health()
    if args.check == "data":
        return check_data()

    for fn in (check_topology, check_health, check_data):
        rc = fn()
        if rc != 0:
            return rc
    print("MongoDB initialize verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
