#!/usr/bin/env python3
import argparse
import base64
import datetime
import json
import os
import subprocess
import sys


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "mongodb")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "mongo-rs")
SERVICE_NAME = os.environ.get("BENCH_PARAM_SERVICE_NAME", "mongo")
OPENSSL_POD = os.environ.get("BENCH_PARAM_OPENSSL_POD_NAME", "openssl-toolbox")
TLS_CA_SECRET = os.environ.get("BENCH_PARAM_TLS_CA_SECRET_NAME", "mongodb-tls-ca")
TLS_CERT_SECRET = os.environ.get("BENCH_PARAM_TLS_CERT_SECRET_NAME", "mongodb-tls-cert")
OLD_CONFIGMAP = os.environ.get("BENCH_PARAM_OLD_TLS_CONFIGMAP_NAME", "mongodb-tls-old")
MIN_VALID_DAYS = int(os.environ.get("BENCH_PARAM_TARGET_VALIDITY_MIN_DAYS", "300"))
MAX_VALID_DAYS = int(os.environ.get("BENCH_PARAM_TARGET_VALIDITY_MAX_DAYS", "400"))
TLS_URI = f"mongodb://{CLUSTER_PREFIX}-0.{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:27017/?directConnection=true&serverSelectionTimeoutMS=4000&connectTimeoutMS=4000"


def run(cmd, input_data=None, text=True):
    return subprocess.run(
        cmd,
        input=input_data,
        text=text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def fail(prefix, errors):
    if errors:
        print(prefix, file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


def get_configmap_value(name, key, errors):
    res = run([
        "kubectl", "-n", NAMESPACE, "get", "configmap", name, "-o", f"jsonpath={{.data.{key}}}"
    ])
    if res.returncode != 0:
        errors.append(f"Failed to read configmap {name}: {res.stderr.strip() or res.stdout.strip()}")
        return None
    return (res.stdout or "").strip()


def get_secret_bytes(secret_name, key, errors):
    res = run([
        "kubectl", "-n", NAMESPACE, "get", "secret", secret_name, "-o", "json"
    ])
    if res.returncode != 0:
        errors.append(f"Failed to read secret {secret_name}: {res.stderr.strip() or res.stdout.strip()}")
        return None
    try:
        data = json.loads(res.stdout).get("data", {})
        val = data.get(key)
    except Exception:
        errors.append(f"Failed to parse secret {secret_name} JSON")
        return None
    if not val:
        errors.append(f"Secret {secret_name} missing key {key}")
        return None
    try:
        return base64.b64decode(val)
    except Exception:
        errors.append(f"Failed to decode secret {secret_name}.{key}")
        return None


def openssl_fingerprint_from_pem(pem_bytes, label, errors):
    res = run(
        [
            "kubectl", "-n", NAMESPACE, "exec", "-i", OPENSSL_POD, "--",
            "openssl", "x509", "-noout", "-fingerprint", "-sha256",
        ],
        input_data=pem_bytes,
        text=False,
    )
    if res.returncode != 0:
        errors.append(f"{label} fingerprint failed: {res.stderr.decode().strip() or res.stdout.decode().strip()}")
        return None
    out = res.stdout.decode().strip()
    if "=" not in out:
        errors.append(f"Unable to parse {label} fingerprint output")
        return None
    return out.split("=", 1)[1].strip()


def openssl_not_after_from_pem(pem_bytes, label, errors):
    res = run(
        [
            "kubectl", "-n", NAMESPACE, "exec", "-i", OPENSSL_POD, "--",
            "openssl", "x509", "-noout", "-enddate",
        ],
        input_data=pem_bytes,
        text=False,
    )
    if res.returncode != 0:
        errors.append(f"{label} enddate failed: {res.stderr.decode().strip() or res.stdout.decode().strip()}")
        return None
    out = res.stdout.decode().strip()
    if not out.lower().startswith("notafter="):
        errors.append(f"Unable to parse {label} notAfter output")
        return None
    return out.split("=", 1)[1].strip()


def parse_not_after(not_after, errors):
    try:
        return datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=datetime.timezone.utc)
    except Exception:
        errors.append(f"Unable to parse notAfter value: {not_after}")
        return None


def check_fingerprints():
    errors = []
    old_server_fp = get_configmap_value(OLD_CONFIGMAP, "server_fingerprint_sha256", errors)
    old_ca_fp = get_configmap_value(OLD_CONFIGMAP, "ca_fingerprint_sha256", errors)

    server_pem = get_secret_bytes(TLS_CERT_SECRET, "server.pem", errors)
    ca_pem = get_secret_bytes(TLS_CA_SECRET, "ca.crt", errors)

    if server_pem is None or ca_pem is None:
        return fail("Certificate rotation fingerprint check failed:", errors)

    cur_server_fp = openssl_fingerprint_from_pem(server_pem, "server", errors)
    cur_ca_fp = openssl_fingerprint_from_pem(ca_pem, "ca", errors)

    if old_server_fp and cur_server_fp and old_server_fp == cur_server_fp:
        errors.append("server certificate fingerprint did not change")
    if old_ca_fp and cur_ca_fp and old_ca_fp != cur_ca_fp:
        errors.append("CA fingerprint changed; CA trust must remain unchanged")

    return fail("Certificate rotation fingerprint check failed:", errors)


def check_validity():
    errors = []
    server_pem = get_secret_bytes(TLS_CERT_SECRET, "server.pem", errors)
    if server_pem is None:
        return fail("Certificate rotation validity check failed:", errors)

    not_after = openssl_not_after_from_pem(server_pem, "server", errors)
    if not_after:
        expires_at = parse_not_after(not_after, errors)
        if expires_at:
            now = datetime.datetime.now(datetime.timezone.utc)
            days_left = (expires_at - now).days
            if days_left < MIN_VALID_DAYS:
                errors.append(f"rotated certificate validity too short: {days_left} days")
            if days_left > MAX_VALID_DAYS:
                errors.append(f"rotated certificate validity too long: {days_left} days")

    return fail("Certificate rotation validity check failed:", errors)


def check_tls():
    errors = []

    tls = run([
        "kubectl", "-n", NAMESPACE, "exec", f"{CLUSTER_PREFIX}-0", "--", "mongosh", "--quiet", TLS_URI,
        "--tls", "--tlsCAFile", "/etc/mongo-ca/ca.crt", "--eval", "db.hello().ok",
    ])
    if tls.returncode != 0:
        errors.append(f"TLS connection failed: {tls.stderr.strip() or tls.stdout.strip()}")

    plain = run([
        "kubectl", "-n", NAMESPACE, "exec", f"{CLUSTER_PREFIX}-0", "--", "mongosh", "--quiet", TLS_URI,
        "--eval", "db.hello().ok",
    ])
    if plain.returncode == 0:
        errors.append("insecure connection unexpectedly succeeded")

    return fail("Certificate rotation TLS check failed:", errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", default="all", choices=["all", "fingerprints", "validity", "tls"])
    args = parser.parse_args()

    if args.check == "fingerprints":
        return check_fingerprints()
    if args.check == "validity":
        return check_validity()
    if args.check == "tls":
        return check_tls()

    for fn in (check_fingerprints, check_validity, check_tls):
        rc = fn()
        if rc != 0:
            return rc
    print("Certificate rotation verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
