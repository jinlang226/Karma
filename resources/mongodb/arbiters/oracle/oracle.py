#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
DATA_CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_DATA_CLUSTER_PREFIX", "mongo-rs")
DATA_SERVICE = os.environ.get("BENCH_PARAM_DATA_SERVICE_NAME", "mongo")
ARBITER_CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_ARBITER_CLUSTER_PREFIX", "mongo-arb")
ARBITER_SERVICE = os.environ.get("BENCH_PARAM_ARBITER_SERVICE_NAME", "mongo-arb")
DATA_REPLICAS = int(os.environ.get("BENCH_PARAM_DATA_REPLICAS", "2"))
ARBITER_REPLICAS = int(os.environ.get("BENCH_PARAM_ARBITER_REPLICAS", "1"))
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
    data_sts = run(["kubectl", "-n", NAMESPACE, "get", "sts", DATA_CLUSTER_PREFIX, "-o", "json"])
    arb_sts = run(["kubectl", "-n", NAMESPACE, "get", "sts", ARBITER_CLUSTER_PREFIX, "-o", "json"])
    if data_sts.returncode != 0:
        detail = data_sts.stderr.strip() or data_sts.stdout.strip() or f"exit {data_sts.returncode}"
        errors.append(f"Failed to read data statefulset: {detail}")
    else:
        try:
            data_obj = json.loads(data_sts.stdout)
        except json.JSONDecodeError:
            errors.append("Failed to parse data statefulset JSON")
            data_obj = {}
        if data_obj.get("spec", {}).get("replicas") != DATA_REPLICAS:
            errors.append(f"Data StatefulSet replicas expected {DATA_REPLICAS}")
        if data_obj.get("status", {}).get("readyReplicas", 0) != DATA_REPLICAS:
            errors.append(f"Data StatefulSet ready replicas expected {DATA_REPLICAS}")

    if arb_sts.returncode != 0:
        detail = arb_sts.stderr.strip() or arb_sts.stdout.strip() or f"exit {arb_sts.returncode}"
        errors.append(f"Failed to read arbiter statefulset: {detail}")
    else:
        try:
            arb_obj = json.loads(arb_sts.stdout)
        except json.JSONDecodeError:
            errors.append("Failed to parse arbiter statefulset JSON")
            arb_obj = {}
        if arb_obj.get("spec", {}).get("replicas") != ARBITER_REPLICAS:
            errors.append(f"Arbiter StatefulSet replicas expected {ARBITER_REPLICAS}")
        if arb_obj.get("status", {}).get("readyReplicas", 0) != ARBITER_REPLICAS:
            errors.append(f"Arbiter StatefulSet ready replicas expected {ARBITER_REPLICAS}")

    return fail("MongoDB arbiters workload check failed:", errors)


def check_topology():
    errors = []
    pod = f"{DATA_CLUSTER_PREFIX}-0"
    conf = mongo_json(pod, "JSON.stringify(rs.conf())", "rs.conf()", errors)
    if isinstance(conf, dict):
        members = conf.get("members", [])
        if len(members) != DATA_REPLICAS + ARBITER_REPLICAS:
            errors.append(f"Expected {DATA_REPLICAS + ARBITER_REPLICAS} members, got {len(members)}")
        arbiters = [m for m in members if m.get("arbiterOnly") is True]
        data_members = [m for m in members if m.get("arbiterOnly") is not True]
        if len(arbiters) != ARBITER_REPLICAS:
            errors.append(f"Expected {ARBITER_REPLICAS} arbiter member, got {len(arbiters)}")
        if len(data_members) != DATA_REPLICAS:
            errors.append(f"Expected {DATA_REPLICAS} data members, got {len(data_members)}")
        expected_arb_host = f"{ARBITER_CLUSTER_PREFIX}-0.{ARBITER_SERVICE}.{NAMESPACE}.svc.cluster.local:27017"
        arb_hosts = {m.get("host") for m in arbiters if m.get("host")}
        if expected_arb_host not in arb_hosts:
            errors.append(f"Arbiter host missing from rs.conf(): {expected_arb_host}")

    status = mongo_json(pod, "JSON.stringify(rs.status())", "rs.status()", errors)
    if isinstance(status, dict):
        members = status.get("members", [])
        primary = sum(1 for m in members if m.get("stateStr") == "PRIMARY")
        secondary = sum(1 for m in members if m.get("stateStr") == "SECONDARY")
        arbiters = sum(1 for m in members if m.get("stateStr") == "ARBITER")
        if primary != 1:
            errors.append(f"Expected 1 PRIMARY, got {primary}")
        if secondary != DATA_REPLICAS - 1:
            errors.append(f"Expected {DATA_REPLICAS - 1} SECONDARY, got {secondary}")
        if arbiters != ARBITER_REPLICAS:
            errors.append(f"Expected {ARBITER_REPLICAS} ARBITER, got {arbiters}")

    expected_data_hosts = {
        f"{DATA_CLUSTER_PREFIX}-{i}.{DATA_SERVICE}.{NAMESPACE}.svc.cluster.local:27017"
        for i in range(DATA_REPLICAS)
    }
    if isinstance(conf, dict):
        data_hosts = {m.get("host") for m in conf.get("members", []) if m.get("arbiterOnly") is not True}
        if data_hosts != expected_data_hosts:
            errors.append(f"Data member hosts mismatch: expected={sorted(expected_data_hosts)} actual={sorted(data_hosts)}")

    return fail("MongoDB arbiters topology check failed:", errors)


def check_data():
    errors = []
    pod = f"{DATA_CLUSTER_PREFIX}-0"
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

    return fail("MongoDB arbiters data check failed:", errors)


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
    print("MongoDB arbiter addition verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
