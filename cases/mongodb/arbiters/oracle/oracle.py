#!/usr/bin/env python3
import argparse
import base64
import json
import os
import subprocess
import sys
import time


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


def run(cmd, timeout=30):
    """Run a command bounded (O17): a hung kubectl/mongosh exec becomes a
    failed attempt instead of an uncaught TimeoutExpired that would crash the
    whole oracle at its deadline."""
    try:
        return subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timed out")


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
    pod = probe_pod or f"{DATA_CLUSTER_PREFIX}-0"
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
    uri = (f"mongodb://{pod}.{DATA_SERVICE}.{NAMESPACE}.svc.cluster.local:27017/"
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
    """Locate the replica-set PRIMARY data pod, falling back to DATA_CLUSTER_PREFIX-0.

    The environment PERSISTS across workflow stages, so an earlier stage (or
    this case's own election) can move the PRIMARY off pod-0. countDocuments
    requires the primary -- on a secondary it fails "not primary and
    secondaryOk=false" (O8). Exec db.hello() into each data member and route
    the data read to the writable primary; standalone this resolves to pod-0
    -> identical behaviour. Copied from decommission's oracle. Cached.
    """
    global _PRIMARY_POD_CACHE
    if _PRIMARY_POD_CACHE is not None:
        return _PRIMARY_POD_CACHE
    for idx in range(9):
        pod = f"{DATA_CLUSTER_PREFIX}-{idx}"
        res = mongo_eval(pod, "db.hello().isWritablePrimary")
        if res.returncode != 0:
            if idx > 0 and "NotFound" in (res.stderr or ""):
                break
            continue
        if "true" in (res.stdout or ""):
            _PRIMARY_POD_CACHE = pod
            return pod
    return f"{DATA_CLUSTER_PREFIX}-0"


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

    return errors


def check_workload():
    # O-flap-restart: adding the arbiter rolls/reconfigures the set, so the
    # data+arbiter readyReplicas tally reads short during the rejoin window.
    # Poll to convergence (~120s, 5s between attempts); assertions unchanged.
    deadline = time.monotonic() + 120
    errors = []
    while True:
        errors = _check_workload_attempt()
        if not errors:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(5)
    return fail("MongoDB arbiters workload check failed:", errors)


def _check_topology_attempt():
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

    return errors


def check_topology():
    # O-flap-restart: the reconfigured/added member sits in a rejoin window,
    # reading the PRIMARY/SECONDARY/ARBITER tally short. Poll to convergence
    # (~120s, 5s between attempts); assertion not loosened.
    deadline = time.monotonic() + 120
    errors = []
    while True:
        errors = _check_topology_attempt()
        if not errors:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(5)
    return fail("MongoDB arbiters topology check failed:", errors)


def check_data():
    errors = []
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
