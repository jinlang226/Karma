#!/usr/bin/env python3
import base64
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_param,
    bench_param_int,
    cluster_pod,
    cluster_prefix,
    kubectl_json,
    run,
)


def _parse_not_after(raw_value):
    text = str(raw_value).strip()
    if text.endswith(" GMT"):
        text = text[:-4]
    return datetime.strptime(text, "%b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)


def _openssl_value(cert_pem_bytes, flag):
    result = subprocess.run(
        ["openssl", "x509", "-noout", flag],
        input=cert_pem_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return None, result.stderr.decode("utf-8", errors="replace").strip() or "openssl failed"
    return result.stdout.decode("utf-8", errors="replace").strip(), None


def _fingerprint(cert_pem_bytes):
    result = subprocess.run(
        ["openssl", "x509", "-noout", "-fingerprint", "-sha256"],
        input=cert_pem_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace").strip() or "openssl failed"
        return None, err
    value = result.stdout.decode("utf-8", errors="replace").strip()
    return value.split("=", 1)[-1].upper().replace(":", ""), None


def _not_after(cert_pem_bytes):
    value, err = _openssl_value(cert_pem_bytes, "-enddate")
    if err:
        return None, err
    text = value.replace("notAfter=", "")
    try:
        return _parse_not_after(text), None
    except ValueError:
        return None, f"Unable to parse cert notAfter: {text}"


def _secret_cert(secret_payload, key):
    raw = (secret_payload.get("data") or {}).get(key)
    if not raw:
        return None, f"Missing secret key {key}"
    try:
        return base64.b64decode(raw), None
    except Exception as exc:
        return None, f"Invalid base64 in secret key {key}: {exc}"


def main():
    namespace = bench_namespace("cockroachdb")
    prefix = cluster_prefix("crdb-cluster")
    pod0 = cluster_pod(prefix, 0)
    cert_secret_name = bench_param("cert_secret_name", "crdb-cluster-certs")
    old_cert_configmap_name = bench_param("old_cert_configmap_name", "crdb-old-cert")
    min_rotated_validity_days = bench_param_int("min_rotated_validity_days", 300)

    errors = []

    secure_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod0,
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

    old_cm, cm_err = kubectl_json(namespace, ["get", "configmap", old_cert_configmap_name])
    old_fp = ""
    old_ca_fp = ""
    old_not_after = None
    if cm_err:
        errors.append(f"Missing old certificate ConfigMap '{old_cert_configmap_name}': {cm_err}")
    else:
        cm_data = old_cm.get("data") or {}
        old_fp = str(cm_data.get("fingerprint") or "").strip().upper().replace(":", "")
        if not old_fp:
            errors.append("Missing fingerprint in old certificate ConfigMap")
        old_ca_fp = str(cm_data.get("ca_fingerprint") or "").strip().upper().replace(":", "")
        if not old_ca_fp:
            errors.append("Missing ca_fingerprint in old certificate ConfigMap")
        raw_not_after = cm_data.get("not_after")
        if not raw_not_after:
            errors.append("Missing not_after in old certificate ConfigMap")
        else:
            try:
                old_not_after = _parse_not_after(raw_not_after)
            except ValueError:
                errors.append(f"Unable to parse old not_after: {raw_not_after}")

    secret, secret_err = kubectl_json(namespace, ["get", "secret", cert_secret_name])
    node_cert = None
    ca_cert = None
    if secret_err:
        errors.append(f"Failed to load certificate secret '{cert_secret_name}': {secret_err}")
    else:
        node_cert, node_err = _secret_cert(secret, "node.crt")
        if node_err:
            errors.append(node_err)
        ca_cert, ca_err = _secret_cert(secret, "ca.crt")
        if ca_err:
            errors.append(ca_err)

    if node_cert:
        new_fp, fp_err = _fingerprint(node_cert)
        if fp_err:
            errors.append(f"Failed to read new node cert fingerprint: {fp_err}")
        elif old_fp and new_fp == old_fp:
            errors.append("Node certificate fingerprint did not change")

        new_not_after, na_err = _not_after(node_cert)
        if na_err:
            errors.append(f"Failed to read new node cert expiration: {na_err}")
        elif old_not_after:
            delta_days = (new_not_after - old_not_after).days
            if delta_days < min_rotated_validity_days:
                errors.append(
                    f"New certificate validity too short (delta {delta_days} days, expected >= {min_rotated_validity_days})"
                )

    if ca_cert:
        ca_new_fp, ca_err = _fingerprint(ca_cert)
        if ca_err:
            errors.append(f"Failed to read new CA fingerprint: {ca_err}")
        elif old_ca_fp and ca_new_fp != old_ca_fp:
            errors.append("CA fingerprint changed; expected same CA")

    if errors:
        print("Certificate rotation verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Certificates rotated successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
