#!/usr/bin/env python3
import argparse
import base64
import json
import os
import subprocess
import sys
import time


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


_ADMIN_PW = None


def _admin_auth_flags():
    """`-u <admin> -p <pw> --authenticationDatabase admin` when the admin secret
    exists. The env PERSISTS across stages, so a prior deploy stage may have
    enabled auth (keyfile + admin user), after which rs.conf()/rs.status()
    require credentials. Empty when no admin secret is present. Cached."""
    global _ADMIN_PW
    if _ADMIN_PW is None:
        secret = os.environ.get("BENCH_PARAM_ADMIN_SECRET_NAME", "admin-user-password")
        r = run(["kubectl", "-n", NAMESPACE, "get", "secret", secret,
                 "-o", "jsonpath={.data.password}"])
        pw = ""
        if r.returncode == 0 and r.stdout.strip():
            try:
                pw = base64.b64decode(r.stdout.strip()).decode()
            except Exception:
                pw = ""
        _ADMIN_PW = pw
    if _ADMIN_PW:
        user = os.environ.get("BENCH_PARAM_ADMIN_USERNAME", "admin")
        return ["-u", user, "-p", _ADMIN_PW, "--authenticationDatabase", "admin"]
    return []


_TLS_FLAGS_CACHE = None


def _mongo_tls_flags(probe_pod=None):
    """mongosh transport flags that adapt to the cluster's LIVE TLS mode.

    The environment PERSISTS across workflow stages, so an earlier stage
    (mongodb/tls-setup or mongodb/certificate-rotation) may have turned TLS on,
    after which a plain mongosh connection is refused. Detect TLS by probing the
    running mongo pod for a CA cert mounted at the paths the TLS stages use; if
    present, connect with --tls --tlsCAFile (and a client cert for mutual TLS
    when one is mounted), else connect plain. Standalone (no CA mounted) this
    returns [] -> identical plain behaviour. It only adds transport flags; every
    real check still runs and still fails when its condition is unmet.
    """
    global _TLS_FLAGS_CACHE
    if _TLS_FLAGS_CACHE is not None:
        return list(_TLS_FLAGS_CACHE)
    flags = []
    pod = probe_pod or f"{CLUSTER_PREFIX}-0"
    ca_path = None
    for cand in (
        "/etc/tls/ca.crt",
        "/etc/mongo-ca/ca.crt",
        "/etc/mongodb/tls/ca.crt",
        "/etc/ssl/mongodb/ca.crt",
    ):
        probe = run(["kubectl", "-n", NAMESPACE, "exec", pod, "--", "/bin/sh", "-c", "test -f " + cand])
        if probe.returncode == 0:
            ca_path = cand
            break
    if ca_path:
        flags = ["--tls", "--tlsAllowInvalidHostnames", "--tlsAllowInvalidCertificates", "--tlsCAFile", ca_path]
        for client_pem in ("/etc/tls/client.pem", "/etc/mongo-ca/client.pem"):
            cprobe = run(["kubectl", "-n", NAMESPACE, "exec", pod, "--", "/bin/sh", "-c", "test -f " + client_pem])
            if cprobe.returncode == 0:
                flags += ["--tlsCertificateKeyFile", client_pem]
                break
    _TLS_FLAGS_CACHE = flags
    return list(flags)

def fail(prefix, errors):
    if errors:
        print(prefix, file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


def mongo_eval(pod, script):
    # Connect via the member's own FQDN with directConnection=true to skip SDAM
    # topology monitoring, which a bare localhost connection would start and
    # which fails under a persisted requireTLS mode. directConnection works on
    # any single member for rs.conf()/rs.status() reads.
    uri = (f"mongodb://{pod}.{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:27017/"
           "?directConnection=true")
    base = ["kubectl", "-n", NAMESPACE, "exec", pod, "--",
            "mongosh", "--quiet", *_mongo_tls_flags(), uri]
    res = run(base + ["--eval", script])
    # Adaptive auth: a deploy stage may have enabled auth, so a plain rs.conf()
    # fails "requires authentication". Retry once with live admin credentials.
    out = ((res.stderr or "") + (res.stdout or "")).lower()
    if res.returncode != 0 and (
        "requires authentication" in out or "not authorized" in out
        or "unauthorized" in out or "authentication failed" in out
    ):
        af = _admin_auth_flags()
        if af:
            res = run(base + af + ["--eval", script])
    return res


_PRIMARY_POD_CACHE = None


def find_primary():
    """Locate the replica-set PRIMARY pod, falling back to CLUSTER_PREFIX-0.

    The environment PERSISTS across workflow stages, so an earlier stage (e.g.
    mongodb/arbiters) can trigger an election that moves the PRIMARY off
    ``{CLUSTER_PREFIX}-0``. The data count read requires the primary -- on a
    secondary it fails with "not primary and secondaryOk=false". Exec
    db.hello() into each member, parse the writable-primary node, and route the
    data read there. (rs.conf()/rs.status() are fine on any node, so the
    topology check is left on -0.) Standalone (single node) this resolves to
    -0 -> identical behaviour; no check or expected value changes.
    """
    global _PRIMARY_POD_CACHE
    if _PRIMARY_POD_CACHE is not None:
        return _PRIMARY_POD_CACHE
    for idx in range(9):
        pod = f"{CLUSTER_PREFIX}-{idx}"
        res = mongo_eval(pod, "db.hello().isWritablePrimary")
        if res.returncode != 0:
            if idx > 0 and "NotFound" in (res.stderr or ""):
                break
            continue
        if "true" in (res.stdout or ""):
            _PRIMARY_POD_CACHE = pod
            return pod
    return f"{CLUSTER_PREFIX}-0"


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


def _check_workload_attempt():
    errors = []
    res = run(["kubectl", "-n", NAMESPACE, "get", "sts", CLUSTER_PREFIX, "-o", "json"])
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        errors.append(f"Failed to read statefulset {CLUSTER_PREFIX}: {detail}")
        return errors
    try:
        sts = json.loads(res.stdout)
    except json.JSONDecodeError:
        errors.append("Failed to parse statefulset JSON")
        return errors

    spec_replicas = sts.get("spec", {}).get("replicas")
    ready_replicas = sts.get("status", {}).get("readyReplicas", 0)
    if spec_replicas != TARGET_REPLICAS:
        errors.append(f"StatefulSet replicas expected {TARGET_REPLICAS}, got {spec_replicas}")
    if ready_replicas != TARGET_REPLICAS:
        errors.append(f"Ready replicas expected {TARGET_REPLICAS}, got {ready_replicas}")

    return errors


def check_workload():
    # O-flap-restart: decommissioning reconfigures the set and removes a pod, so
    # the readyReplicas/pod-count tally reads short during the rejoin/election
    # window. Poll to convergence (~120s, 5s between attempts); assertions
    # unchanged.
    deadline = time.monotonic() + 120
    errors = []
    while True:
        errors = _check_workload_attempt()
        if not errors:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(5)
    return fail("MongoDB decommission workload check failed:", errors)


def _check_topology_attempt():
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

    return errors


def check_topology():
    # O-flap-restart: after the decommission reconfig the set may re-elect and a
    # member may briefly RECOVER, reading the SECONDARY tally short. Poll to
    # convergence (~120s, 5s between attempts); assertion not loosened.
    deadline = time.monotonic() + 120
    errors = []
    while True:
        errors = _check_topology_attempt()
        if not errors:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(5)
    return fail("MongoDB decommission topology check failed:", errors)


def check_data():
    errors = []
    # Count must run on the PRIMARY (secondaryOk=false), which a workflow
    # election may have moved off CLUSTER_PREFIX-0.
    pod = find_primary()
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
