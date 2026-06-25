#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongo-rs")
SERVICE_NAME = os.environ.get("BENCH_PARAM_SERVICE_NAME", "mongo")
POD = f"{CLUSTER_PREFIX}-0"
TLS_CA_SECRET = os.environ.get("BENCH_PARAM_TLS_CA_SECRET_NAME", "mongodb-tls-ca")
TLS_CERT_SECRET = os.environ.get("BENCH_PARAM_TLS_CERT_SECRET_NAME", "mongodb-tls-cert")
APP_DB = os.environ.get("BENCH_PARAM_APP_DATABASE", "app")
APP_COLLECTION = os.environ.get("BENCH_PARAM_APP_COLLECTION", "test")
SEED_DOCS = int(os.environ.get("BENCH_PARAM_SEED_DOCS", "3"))
TLS_URI = f"mongodb://{POD}.{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:27017/?directConnection=true&serverSelectionTimeoutMS=4000&connectTimeoutMS=4000"


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def fail(prefix, errors):
    if errors:
        print(prefix, file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


def check_plain_blocked():
    errors = []
    plain = run([
        "kubectl", "-n", NAMESPACE, "exec", POD, "--", "mongosh", "--quiet", "--eval", "db.adminCommand({ping:1})"
    ])
    if plain.returncode == 0:
        errors.append("plain connection succeeded; TLS is not required")
    return fail("TLS setup plain-blocked check failed:", errors)


def tls_base_cmd(eval_str):
    return [
        "kubectl", "-n", NAMESPACE, "exec", POD, "--", "mongosh", "--quiet", TLS_URI,
        "--tls", "--tlsCAFile", "/etc/mongo-ca/ca.crt", "--eval", eval_str,
    ]


def check_tls_ok():
    errors = []
    ping = run(tls_base_cmd("db.adminCommand({ping:1}).ok"))
    if ping.returncode != 0:
        errors.append(f"TLS ping failed: {ping.stderr.strip() or ping.stdout.strip()}")

    status = run(tls_base_cmd("rs.status().ok"))
    if status.returncode != 0:
        errors.append(f"TLS rs.status failed: {status.stderr.strip() or status.stdout.strip()}")
    elif (status.stdout or "").strip() != "1":
        errors.append(f"TLS rs.status unexpected: {(status.stdout or '').strip()}")

    return fail("TLS setup tls-ok check failed:", errors)


def check_data():
    errors = []
    count = run(
        tls_base_cmd(
            f'db.getMongo().setReadPref("secondaryPreferred");'
            f'db.getSiblingDB("{APP_DB}").{APP_COLLECTION}.countDocuments({{}})'
        )
    )
    if count.returncode != 0:
        errors.append(f"TLS data count failed: {count.stderr.strip() or count.stdout.strip()}")
    else:
        raw = (count.stdout or "").strip()
        if not raw.isdigit() or int(raw) < SEED_DOCS:
            errors.append(f"expected >= {SEED_DOCS} docs in {APP_DB}.{APP_COLLECTION}, got {raw}")

    return fail("TLS setup data check failed:", errors)


def check_wiring():
    errors = []
    for secret_name in (TLS_CA_SECRET, TLS_CERT_SECRET):
        result = run(["kubectl", "-n", NAMESPACE, "get", "secret", secret_name])
        if result.returncode != 0:
            errors.append(f"TLS Secret {secret_name} is missing")

    result = run(
        ["kubectl", "-n", NAMESPACE, "get", "statefulset", CLUSTER_PREFIX, "-o", "json"]
    )
    if result.returncode != 0:
        errors.append(f"Failed to read StatefulSet {CLUSTER_PREFIX}")
    else:
        try:
            statefulset = json.loads(result.stdout)
        except json.JSONDecodeError:
            errors.append(f"Failed to parse StatefulSet {CLUSTER_PREFIX}")
        else:
            volumes = (
                statefulset.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("volumes", [])
                or []
            )
            secret_refs = {
                str((volume.get("secret") or {}).get("secretName") or "")
                for volume in volumes
                if volume.get("secret")
            }
            for secret_name in (TLS_CA_SECRET, TLS_CERT_SECRET):
                if secret_name not in secret_refs:
                    errors.append(
                        f"StatefulSet {CLUSTER_PREFIX} does not reference TLS Secret {secret_name}"
                    )
    return fail("TLS setup wiring check failed:", errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        default="all",
        choices=["all", "plain_blocked", "tls_ok", "data", "wiring"],
    )
    args = parser.parse_args()

    if args.check == "plain_blocked":
        return check_plain_blocked()
    if args.check == "tls_ok":
        return check_tls_ok()
    if args.check == "data":
        return check_data()
    if args.check == "wiring":
        return check_wiring()

    for fn in (check_plain_blocked, check_tls_ok, check_data, check_wiring):
        rc = fn()
        if rc != 0:
            return rc
    print("MongoDB TLS setup verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
