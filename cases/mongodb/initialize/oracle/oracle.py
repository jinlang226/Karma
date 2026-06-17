#!/usr/bin/env python3
import argparse
import base64
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongodb-replica")
HEADLESS_SERVICE = os.environ.get("BENCH_PARAM_HEADLESS_SERVICE_NAME", "mongodb-replica-svc")
REPLICA_SET_NAME = os.environ.get("BENCH_PARAM_REPLICA_SET_NAME", CLUSTER_PREFIX)
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
    require credentials. Empty when no admin secret is present (standalone,
    pre-auth) so the plain connection is used. Cached."""
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
        user = os.environ.get("BENCH_PARAM_ADMIN_USERNAME", "admin-user")
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

def _resolve_expected_replicas():
    """Topology size to enforce.

    The environment PERSISTS across workflow stages, so an earlier
    replica-scaling stage may have grown the replica set past the standalone
    default of 3. Resolve the expected count from (in priority order): an
    explicit ``expected_replicas``/``target_replicas`` param override, else the
    LIVE StatefulSet (ready, else spec'd replicas), else the standalone default
    of 3. This adapts the topology/count check to whatever the workflow
    accumulated without loosening it -- a non-solving agent that drops or fails
    a member still mismatches the live ready/spec count.
    """
    for key in ("BENCH_PARAM_EXPECTED_REPLICAS", "BENCH_PARAM_TARGET_REPLICAS"):
        val = os.environ.get(key)
        if val is not None and str(val).strip():
            try:
                return int(val)
            except ValueError:
                pass
    res = run(["kubectl", "-n", NAMESPACE, "get", "sts", CLUSTER_PREFIX, "-o", "json"])
    if res.returncode == 0:
        try:
            sts = json.loads(res.stdout)
            status = sts.get("status", {}) or {}
            spec = sts.get("spec", {}) or {}
            live = status.get("readyReplicas")
            if not isinstance(live, int) or live <= 0:
                live = spec.get("replicas")
            if isinstance(live, int) and live > 0:
                return live
        except (json.JSONDecodeError, AttributeError):
            pass
    return 3


EXPECTED_REPLICAS = _resolve_expected_replicas()


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
    uri = (f"mongodb://{pod}.{HEADLESS_SERVICE}.{NAMESPACE}.svc.cluster.local:27017/"
           "?directConnection=true&serverSelectionTimeoutMS=4000&connectTimeoutMS=4000")
    base = ["kubectl", "-n", NAMESPACE, "exec", pod, "--",
            "mongosh", "--quiet", *_mongo_tls_flags(), uri]
    res = run(base + ["--eval", script])
    # Adaptive auth: a deploy stage may have enabled auth, so a plain rs.conf()
    # fails "requires authentication". Retry once with the live admin credentials.
    out = ((res.stderr or "") + (res.stdout or "")).lower()
    if res.returncode != 0 and (
        "requires authentication" in out or "not authorized" in out
        or "unauthorized" in out or "authentication failed" in out
    ):
        af = _admin_auth_flags()
        if af:
            res = run(base + af + ["--eval", script])
    return res


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
