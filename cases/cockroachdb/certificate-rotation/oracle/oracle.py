#!/usr/bin/env python3
# Verify the TLS certs were rotated (secure SQL works, node/CA fingerprints,
# ~1y validity). The cert secret name (BENCH_PARAM_CERT_SECRET_NAME) and the
# pre-rotation fingerprint ConfigMap name (BENCH_PARAM_OLD_CERT_CONFIGMAP_NAME)
# come from the case params, so a workflow that overrides them is honored
# instead of a hardcoded value. Standalone (default params) this behaves
# identically.
import os
import subprocess
import sys
from datetime import datetime, timezone


CERT_SECRET = os.environ.get("BENCH_PARAM_CERT_SECRET_NAME", "crdb-cluster-certs")
OLD_CERT_CM = os.environ.get("BENCH_PARAM_OLD_CERT_CONFIGMAP_NAME", "crdb-old-cert")


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main():
    errors = []

    secure_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "exec",
        "crdb-cluster-0",
        "--",
        "./cockroach",
        "sql",
        "--certs-dir=/cockroach/cockroach-certs",
        "-e",
        "SELECT 1;",
    ]
    secure_result = run(secure_cmd)
    if secure_result.returncode != 0:
        errors.append("TLS connection failed after certificate rotation")
        errors.append(f"Error: {secure_result.stderr.strip()}")

    old_fp_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "get",
        "configmap",
        OLD_CERT_CM,
        "-o",
        "jsonpath={.data.fingerprint}",
    ]
    old_fp_result = run(old_fp_cmd)
    old_fp = ""
    if old_fp_result.returncode != 0:
        errors.append(f"Missing {OLD_CERT_CM} ConfigMap")
        errors.append(f"Error: {old_fp_result.stderr.strip()}")
    else:
        old_fp = old_fp_result.stdout.strip().upper().replace(":", "")

    old_na_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "get",
        "configmap",
        OLD_CERT_CM,
        "-o",
        "jsonpath={.data.not_after}",
    ]
    old_na_result = run(old_na_cmd)
    old_not_after = None
    if old_na_result.returncode != 0:
        errors.append(f"Missing not_after in {OLD_CERT_CM} ConfigMap")
        errors.append(f"Error: {old_na_result.stderr.strip()}")
    else:
        raw = old_na_result.stdout.strip()
        if raw.endswith(" GMT"):
            raw = raw[:-4]
        try:
            old_not_after = datetime.strptime(raw, "%b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            errors.append(f"Unable to parse old not_after: {old_na_result.stdout.strip()}")

    new_fp_cmd = [
        "/bin/sh",
        "-c",
        f"kubectl -n cockroachdb get secret {CERT_SECRET} "
        "-o jsonpath='{.data.node\\.crt}' | base64 -d | "
        "openssl x509 -noout -fingerprint -sha256",
    ]
    new_fp_result = run(new_fp_cmd)
    new_fp = ""
    if new_fp_result.returncode != 0:
        errors.append("Failed to read new node cert fingerprint")
        errors.append(f"Error: {new_fp_result.stderr.strip()}")
    else:
        new_fp = new_fp_result.stdout.strip().split("=", 1)[-1].upper().replace(":", "")
        if old_fp and new_fp == old_fp:
            errors.append("Node certificate fingerprint did not change")

    new_na_cmd = [
        "/bin/sh",
        "-c",
        f"kubectl -n cockroachdb get secret {CERT_SECRET} "
        "-o jsonpath='{.data.node\\.crt}' | base64 -d | "
        "openssl x509 -noout -enddate",
    ]
    ca_old_cmd = [
        "kubectl",
        "-n",
        "cockroachdb",
        "get",
        "configmap",
        OLD_CERT_CM,
        "-o",
        "jsonpath={.data.ca_fingerprint}",
    ]
    ca_old_result = run(ca_old_cmd)
    ca_old_fp = ""
    if ca_old_result.returncode != 0:
        errors.append(f"Missing ca_fingerprint in {OLD_CERT_CM} ConfigMap")
        errors.append(f"Error: {ca_old_result.stderr.strip()}")
    else:
        ca_old_fp = ca_old_result.stdout.strip().upper().replace(":", "")

    ca_new_cmd = [
        "/bin/sh",
        "-c",
        f"kubectl -n cockroachdb get secret {CERT_SECRET} "
        "-o jsonpath='{.data.ca\\.crt}' | base64 -d | "
        "openssl x509 -noout -fingerprint -sha256",
    ]
    ca_new_result = run(ca_new_cmd)
    if ca_new_result.returncode != 0:
        errors.append("Failed to read new CA fingerprint")
        errors.append(f"Error: {ca_new_result.stderr.strip()}")
    else:
        ca_new_fp = ca_new_result.stdout.strip().split("=", 1)[-1].upper().replace(":", "")
        if ca_old_fp and ca_new_fp != ca_old_fp:
            errors.append("CA fingerprint changed; expected same CA")
    new_na_result = run(new_na_cmd)
    if new_na_result.returncode != 0:
        errors.append("Failed to read new node cert expiration")
        errors.append(f"Error: {new_na_result.stderr.strip()}")
    else:
        raw = new_na_result.stdout.strip().replace("notAfter=", "")
        if raw.endswith(" GMT"):
            raw = raw[:-4]
        try:
            new_not_after = datetime.strptime(raw, "%b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
            if old_not_after:
                delta_days = (new_not_after - old_not_after).days
                if delta_days < 300:
                    errors.append(f"New certificate validity too short (delta {delta_days} days)")
        except ValueError:
            errors.append(f"Unable to parse new not_after: {new_na_result.stdout.strip()}")

    if errors:
        print("Certificate rotation verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Certificates rotated successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
