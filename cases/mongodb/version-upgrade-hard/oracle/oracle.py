#!/usr/bin/env python3
import argparse
import base64
import json
import os
import subprocess
import sys
import time


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongodb-replica")
TO_IMAGE = os.environ.get("BENCH_PARAM_TO_IMAGE", "mongo:7.0.5")
TO_VERSION_PREFIX = os.environ.get("BENCH_PARAM_TO_VERSION_PREFIX", "7.0")
TO_FCV = os.environ.get("BENCH_PARAM_TO_FCV", "7.0")
ADMIN_SECRET_NAME = os.environ.get("BENCH_PARAM_ADMIN_SECRET_NAME", "admin-user-password")
ADMIN_USERNAME = os.environ.get("BENCH_PARAM_ADMIN_USERNAME", "admin-user")
APP_DATABASE = os.environ.get("BENCH_PARAM_APP_DATABASE", "testdb")
APP_COLLECTION = os.environ.get("BENCH_PARAM_APP_COLLECTION", "data")
SEED_MIN_DOCS = int(os.environ.get("BENCH_PARAM_SEED_MIN_DOCS", "1"))


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


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


def get_secret_value(secret_name, key, errors):
    res = run(["kubectl", "-n", NAMESPACE, "get", "secret", secret_name, "-o", f"jsonpath={{.data.{key}}}"])
    if res.returncode != 0:
        detail = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        errors.append(f"Failed to read secret {secret_name}: {detail}")
        return None
    raw = (res.stdout or "").strip()
    if not raw:
        errors.append(f"Secret {secret_name}.{key} is empty")
        return None
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception:
        errors.append(f"Failed to decode secret {secret_name}.{key}")
        return None


def run_mongo(pod, uri, eval_str):
    # Retry the READ. The replica set is often still settling when the oracle
    # runs -- a prior stage rolling-restarts the members right before submitting
    # -- and under a loaded requireTLS cluster the mongosh monitor connection can
    # drop mid-read ("connection <monitor> ... closed"). Those are TRANSIENT
    # transport failures that clear within seconds, so retry before giving up.
    # This never masks a wrong value: a successful read returns the real output
    # and the caller's assertions still fail on any mismatch. When the cluster is
    # quiet the first attempt succeeds and it returns immediately (no sleeps).
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "exec",
        pod,
        "--",
        "mongosh",
        "--quiet",
        *_mongo_tls_flags(),
        uri,
        "--eval",
        eval_str,
    ]
    res = None
    for attempt in range(5):
        res = run(cmd)
        if res.returncode == 0 and (res.stdout or "").strip():
            return res
        if attempt < 4:
            time.sleep(3)
    return res


def load_json(pod, uri, eval_str, label, errors):
    res = run_mongo(pod, uri, eval_str)
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


def find_primary(admin_uri, errors):
    for idx in range(EXPECTED_REPLICAS):
        pod = f"{CLUSTER_PREFIX}-{idx}"
        res = run_mongo(pod, admin_uri, "db.hello().isWritablePrimary")
        if res.returncode == 0 and "true" in (res.stdout or ""):
            return pod
    errors.append("Unable to locate primary pod")
    return f"{CLUSTER_PREFIX}-0"


def check_workload():
    errors = []
    sts_res = run(["kubectl", "-n", NAMESPACE, "get", "sts", CLUSTER_PREFIX, "-o", "json"])
    if sts_res.returncode != 0:
        detail = sts_res.stderr.strip() or sts_res.stdout.strip() or f"exit {sts_res.returncode}"
        errors.append(f"Failed to read statefulset {CLUSTER_PREFIX}: {detail}")
        return fail("Version upgrade workload check failed:", errors)

    try:
        sts = json.loads(sts_res.stdout)
    except json.JSONDecodeError:
        errors.append("Failed to parse statefulset JSON")
        return fail("Version upgrade workload check failed:", errors)

    spec_replicas = sts.get("spec", {}).get("replicas")
    ready_replicas = sts.get("status", {}).get("readyReplicas")
    if spec_replicas != EXPECTED_REPLICAS:
        errors.append(f"StatefulSet replicas expected {EXPECTED_REPLICAS}, got {spec_replicas}")
    if ready_replicas != EXPECTED_REPLICAS:
        errors.append(f"Ready replicas expected {EXPECTED_REPLICAS}, got {ready_replicas}")

    containers = sts.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    if not containers:
        errors.append("StatefulSet template has no containers")
    else:
        image = containers[0].get("image")
        if image != TO_IMAGE:
            errors.append(f"StatefulSet image expected {TO_IMAGE}, got {image}")

    pods_res = run(["kubectl", "-n", NAMESPACE, "get", "pods", "-l", f"app={CLUSTER_PREFIX}", "-o", "json"])
    if pods_res.returncode != 0:
        detail = pods_res.stderr.strip() or pods_res.stdout.strip() or f"exit {pods_res.returncode}"
        errors.append(f"Failed to list pods: {detail}")
        return fail("Version upgrade workload check failed:", errors)

    try:
        pods = json.loads(pods_res.stdout)
    except json.JSONDecodeError:
        errors.append("Failed to parse pod list JSON")
        return fail("Version upgrade workload check failed:", errors)

    items = pods.get("items", [])
    if len(items) != EXPECTED_REPLICAS:
        errors.append(f"Expected {EXPECTED_REPLICAS} pods, found {len(items)}")

    for item in items:
        name = item.get("metadata", {}).get("name", "unknown")
        c = item.get("spec", {}).get("containers", [])
        if not c:
            errors.append(f"Pod {name} has no containers")
            continue
        if c[0].get("image") != TO_IMAGE:
            errors.append(f"Pod {name} image expected {TO_IMAGE}, got {c[0].get('image')}")

    return fail("Version upgrade workload check failed:", errors)


def check_version():
    errors = []
    admin_pw = get_secret_value(ADMIN_SECRET_NAME, "password", errors)
    if admin_pw is None:
        return fail("Version upgrade version check failed:", errors)

    # directConnection skips SDAM topology monitoring, which a localhost
    # connection would start and which fails under a persisted requireTLS mode.
    admin_uri = f"mongodb://{ADMIN_USERNAME}:{admin_pw}@localhost:27017/admin?directConnection=true"
    primary = find_primary(admin_uri, errors)

    version = load_json(primary, admin_uri, "JSON.stringify(db.version())", "db.version()", errors)
    if isinstance(version, str):
        if not version.startswith(TO_VERSION_PREFIX):
            errors.append(f"db.version() expected prefix {TO_VERSION_PREFIX}, got {version}")
    else:
        errors.append("Unable to read db.version()")

    fcv = load_json(
        primary,
        admin_uri,
        "JSON.stringify(db.adminCommand({getParameter:1,featureCompatibilityVersion:1}).featureCompatibilityVersion.version)",
        "featureCompatibilityVersion",
        errors,
    )
    if isinstance(fcv, str):
        if fcv != TO_FCV:
            errors.append(f"FCV expected {TO_FCV}, got {fcv}")
    else:
        errors.append("Unable to read FCV")

    return fail("Version upgrade version check failed:", errors)


def check_topology():
    errors = []
    admin_pw = get_secret_value(ADMIN_SECRET_NAME, "password", errors)
    if admin_pw is None:
        return fail("Version upgrade topology check failed:", errors)

    # directConnection skips SDAM topology monitoring (see check_version).
    admin_uri = f"mongodb://{ADMIN_USERNAME}:{admin_pw}@localhost:27017/admin?directConnection=true"
    primary = find_primary(admin_uri, errors)
    status = load_json(primary, admin_uri, "JSON.stringify(rs.status())", "rs.status()", errors)
    if isinstance(status, dict):
        members = status.get("members", [])
        if len(members) != EXPECTED_REPLICAS:
            errors.append(f"Replica members expected {EXPECTED_REPLICAS}, got {len(members)}")
        primary_n = sum(1 for m in members if m.get("stateStr") == "PRIMARY")
        secondary_n = sum(1 for m in members if m.get("stateStr") == "SECONDARY")
        if primary_n != 1:
            errors.append(f"Expected 1 PRIMARY, got {primary_n}")
        if secondary_n != EXPECTED_REPLICAS - 1:
            errors.append(f"Expected {EXPECTED_REPLICAS - 1} SECONDARY, got {secondary_n}")
    else:
        errors.append("Unable to read replica set status")

    return fail("Version upgrade topology check failed:", errors)


def check_data():
    errors = []
    admin_pw = get_secret_value(ADMIN_SECRET_NAME, "password", errors)
    if admin_pw is None:
        return fail("Version upgrade data check failed:", errors)

    # directConnection skips SDAM topology monitoring (see check_version).
    admin_uri = f"mongodb://{ADMIN_USERNAME}:{admin_pw}@localhost:27017/admin?directConnection=true"
    primary = find_primary(admin_uri, errors)

    count = load_json(
        primary,
        admin_uri,
        f"JSON.stringify(db.getSiblingDB('{APP_DATABASE}').{APP_COLLECTION}.countDocuments({{}}))",
        "seed data count",
        errors,
    )
    if isinstance(count, int):
        if count < SEED_MIN_DOCS:
            errors.append(f"Expected >= {SEED_MIN_DOCS} docs in {APP_DATABASE}.{APP_COLLECTION}, got {count}")
    elif isinstance(count, str) and count.isdigit():
        if int(count) < SEED_MIN_DOCS:
            errors.append(f"Expected >= {SEED_MIN_DOCS} docs in {APP_DATABASE}.{APP_COLLECTION}, got {count}")
    else:
        errors.append("Unable to verify seed data count")

    return fail("Version upgrade data check failed:", errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", default="all", choices=["all", "workload", "version", "topology", "data"])
    args = parser.parse_args()

    if args.check == "workload":
        return check_workload()
    if args.check == "version":
        return check_version()
    if args.check == "topology":
        return check_topology()
    if args.check == "data":
        return check_data()

    for fn in (check_workload, check_version, check_topology, check_data):
        rc = fn()
        if rc != 0:
            return rc
    print("Version upgrade verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
