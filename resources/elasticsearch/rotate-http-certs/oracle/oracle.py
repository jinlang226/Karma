#!/usr/bin/env python3
import base64
import datetime
import json
import os
import subprocess
import sys
import tempfile

NAMESPACE = os.environ.get("BENCH_NAMESPACE", "elasticsearch")
SECRET = os.environ.get("BENCH_PARAM_TLS_SECRET_NAME", "es-http-tls")
OLD_CM = os.environ.get("BENCH_PARAM_OLD_FINGERPRINT_CONFIGMAP_NAME", "es-http-old")
CLIENT_CA_CM = os.environ.get("BENCH_PARAM_HTTP_CA_CONFIGMAP_NAME", "es-http-ca")
SERVICE = os.environ.get("BENCH_PARAM_HTTP_SERVICE_NAME", "es-http")
CURL_POD = os.environ.get("BENCH_PARAM_CURL_POD_NAME", "curl-test")
ELASTIC_USER = os.environ.get("BENCH_PARAM_ELASTIC_USERNAME", "elastic")
ELASTIC_PASS = os.environ.get("BENCH_PARAM_ELASTIC_PASSWORD", "elasticpass")
MIN_VALID_DAYS = int(os.environ.get("BENCH_PARAM_MIN_ROTATED_VALIDITY_DAYS", "300"))
MAX_VALID_DAYS = int(os.environ.get("BENCH_PARAM_MAX_ROTATED_VALIDITY_DAYS", "400"))


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def get_secret_data(key, errors):
    json_key = key.replace(".", "\\.")
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "secret",
            SECRET,
            "-o",
            f"jsonpath={{.data.{json_key}}}",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to read secret {SECRET} {key}: {detail}")
        return None
    raw = result.stdout.strip()
    if not raw:
        errors.append(f"Secret {SECRET} missing key {key}")
        return None
    try:
        return base64.b64decode(raw)
    except base64.binascii.Error:
        errors.append(f"Secret {SECRET} key {key} is not valid base64")
        return None


def get_configmap_text(name, key, errors):
    json_key = key.replace(".", "\\.")
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "get",
            "configmap",
            name,
            "-o",
            f"jsonpath={{.data.{json_key}}}",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to read configmap {name} {key}: {detail}")
        return None
    return result.stdout.strip()


def openssl_fingerprint(path, errors, label):
    result = run(["openssl", "x509", "-noout", "-fingerprint", "-sha256", "-in", path])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to read fingerprint for {label}: {detail}")
        return None
    line = result.stdout.strip()
    if "=" not in line:
        errors.append(f"Unexpected fingerprint output for {label}: {line}")
        return None
    return line.split("=", 1)[1].strip()


def openssl_not_after(path, errors):
    result = run(["openssl", "x509", "-noout", "-enddate", "-in", path])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"Failed to read NotAfter from leaf cert: {detail}")
        return None
    line = result.stdout.strip()
    if not line.startswith("notAfter="):
        errors.append(f"Unexpected NotAfter output: {line}")
        return None
    value = line.split("=", 1)[1].strip()
    try:
        ts = datetime.datetime.strptime(value, "%b %d %H:%M:%S %Y %Z")
    except ValueError:
        errors.append(f"Unable to parse NotAfter date: {value}")
        return None
    return ts.replace(tzinfo=datetime.timezone.utc)


def verify_cert(ca_path, cert_path):
    return run(["openssl", "verify", "-CAfile", ca_path, cert_path])


def curl_health(errors):
    result = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            CURL_POD,
            "--",
            "curl",
            "-s",
            "-S",
            "--max-time",
            "5",
            "--cacert",
            "/etc/es-http-ca/ca.crt",
            "-u",
            f"{ELASTIC_USER}:{ELASTIC_PASS}",
            f"https://{SERVICE}:9200/_cluster/health?wait_for_status=yellow&timeout=5s",
        ]
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errors.append(f"HTTPS health check failed: {detail}")
        return
    output = result.stdout.strip()
    if not output:
        errors.append("HTTPS health check returned empty response")
        return
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        errors.append("HTTPS health check returned invalid JSON")
        return
    status = data.get("status")
    if status not in {"yellow", "green"}:
        errors.append(f"Cluster health expected yellow/green, got {status}")


def main():
    errors = []

    old_ca_fp = get_configmap_text(OLD_CM, "ca_fingerprint_sha256", errors)
    old_leaf_fp = get_configmap_text(OLD_CM, "leaf_fingerprint_sha256", errors)
    old_ca_pem = get_configmap_text(OLD_CM, "ca.crt", errors)
    client_ca_pem = get_configmap_text(CLIENT_CA_CM, "ca.crt", errors)

    if errors:
        print("HTTP cert rotation verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmpdir:
        ca_path = f"{tmpdir}/ca.crt"
        leaf_path = f"{tmpdir}/tls.crt"
        old_ca_path = f"{tmpdir}/old-ca.crt"
        client_ca_path = f"{tmpdir}/client-ca.crt"

        new_ca = get_secret_data("ca.crt", errors)
        new_leaf = get_secret_data("tls.crt", errors)

        if new_ca is not None:
            with open(ca_path, "wb") as f:
                f.write(new_ca)
        if new_leaf is not None:
            with open(leaf_path, "wb") as f:
                f.write(new_leaf)
        if old_ca_pem is not None:
            with open(old_ca_path, "w", encoding="utf-8") as f:
                f.write(old_ca_pem)
        if client_ca_pem is not None:
            with open(client_ca_path, "w", encoding="utf-8") as f:
                f.write(client_ca_pem)

        if new_ca is None or new_leaf is None:
            errors.append("Missing TLS data from rotated secret")
        else:
            new_ca_fp = openssl_fingerprint(ca_path, errors, "new CA")
            new_leaf_fp = openssl_fingerprint(leaf_path, errors, "new leaf")
            if new_ca_fp and old_ca_fp and new_ca_fp == old_ca_fp:
                errors.append("CA fingerprint did not change")
            if new_leaf_fp and old_leaf_fp and new_leaf_fp == old_leaf_fp:
                errors.append("Leaf fingerprint did not change")

            if new_ca_fp and client_ca_pem:
                client_fp = openssl_fingerprint(client_ca_path, errors, "client CA")
                if client_fp and client_fp != new_ca_fp:
                    errors.append("Client CA does not match rotated CA")

            not_after = openssl_not_after(leaf_path, errors)
            if not_after:
                now = datetime.datetime.now(datetime.timezone.utc)
                days_remaining = (not_after - now).days
                if days_remaining < MIN_VALID_DAYS or days_remaining > MAX_VALID_DAYS:
                    errors.append(
                        f"Leaf validity {days_remaining} days out of expected range ({MIN_VALID_DAYS}-{MAX_VALID_DAYS})"
                    )

            verify_new = verify_cert(ca_path, leaf_path)
            if verify_new.returncode != 0:
                detail = verify_new.stderr.strip() or verify_new.stdout.strip()
                errors.append(f"Leaf does not verify with new CA: {detail}")

            verify_old = verify_cert(old_ca_path, leaf_path)
            if verify_old.returncode == 0:
                errors.append("Leaf still verifies with old CA")

        curl_health(errors)

    if errors:
        print("HTTP cert rotation verification failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("HTTP cert rotation verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
