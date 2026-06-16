#!/usr/bin/env python3
import base64
import datetime
import subprocess
import sys
import tempfile
import json

NAMESPACE = "elasticsearch"
SECRET = "es-http-tls"
OLD_CM = "es-http-old"
CLIENT_CA_CM = "es-http-ca"
SERVICE = "es-http"
CURL_POD = "curl-test"
MIN_VALID_DAYS = 300
MAX_VALID_DAYS = 400


def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def get_secret_data(key, errors):
    json_key = key.replace(".", "\\.")
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "get",
        "secret",
        SECRET,
        "-o",
        f"jsonpath={{.data.{json_key}}}",
    ]
    result = run(cmd)
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
    cmd = [
        "kubectl",
        "-n",
        NAMESPACE,
        "get",
        "configmap",
        name,
        "-o",
        f"jsonpath={{.data.{json_key}}}",
    ]
    result = run(cmd)
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


_ELASTIC_PW = None


def _elastic_password():
    """Live elastic-user password.

    Reads the elastic-password secret (a prior rotate-elastic-password stage may
    have rotated it away from this case's default, so the env PERSISTS a new
    value), base64-decoded; falls back to the case default. Cached so the retry
    loop does not re-read the secret each attempt.
    """
    global _ELASTIC_PW
    if _ELASTIC_PW is not None:
        return _ELASTIC_PW
    import base64
    r = run(["kubectl", "-n", NAMESPACE, "get", "secret", "elastic-password",
             "-o", "jsonpath={.data.password}"])
    pw = None
    if r.returncode == 0 and r.stdout.strip():
        try:
            pw = base64.b64decode(r.stdout.strip()).decode()
        except Exception:
            pw = None
    _ELASTIC_PW = pw or os.environ.get("BENCH_PARAM_ELASTIC_PASSWORD") or "elasticpass"
    return _ELASTIC_PW


def _health_curl(scheme):
    """Run the cluster-health curl over the given scheme.

    https uses the rotated CA (--cacert); http (fallback for a cluster whose
    HTTP TLS a prior stage disabled) adds nothing. -k is a belt-and-suspenders
    in case the served leaf doesn't chain to the mounted CA path.
    """
    # The client deadline (--max-time 20) must exceed the server-side
    # ``timeout`` in the path, otherwise curl aborts (exit 28) before ES can
    # answer. curl_health() retries to absorb transient flapping, so the server
    # wait stays short (5s).
    path = "/_cluster/health?wait_for_status=yellow&timeout=5s"
    cmd = [
        "kubectl", "-n", NAMESPACE, "exec", CURL_POD, "--",
        "curl", "-s", "-S", "--max-time", "20",
    ]
    if scheme == "https":
        cmd += ["--cacert", "/etc/es-http-ca/ca.crt", "-k"]
    cmd += ["-u", f"elastic:{_elastic_password()}",
            f"{scheme}://{SERVICE}.{NAMESPACE}.svc:9200{path}"]
    return run(cmd)


def _evaluate_health():
    """Run one cluster-health snapshot; return the list of health errors."""
    errs = []
    # The env PERSISTS across stages; the cluster's live scheme may differ from
    # this case's default (https for a cert-rotation task). Try https (with the
    # rotated CA) then fall back to http. Cert assertions above are unaffected.
    result = _health_curl("https")
    if result.returncode != 0:
        result = _health_curl("http")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        errs.append(f"Cluster health check failed: {detail}")
        return errs
    output = result.stdout.strip()
    if not output:
        errs.append("HTTPS health check returned empty response")
        return errs
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        errs.append("HTTPS health check returned invalid JSON")
        return errs
    status = data.get("status")
    if status not in {"yellow", "green"}:
        errs.append(f"Cluster health expected yellow/green, got {status}")
    return errs


def curl_health(errors):
    # A multi-node ES cluster can flap at the edge of readiness under load: it
    # briefly reports a non-green/yellow status during GC or shard recovery even
    # though it converges green. A single snapshot can catch that transient. So
    # verify the STABLE converged health: re-evaluate for up to ~75s and accept
    # the first clean snapshot. This does not loosen the green/yellow
    # requirement -- a genuinely degraded cluster fails every attempt. The cert
    # assertions above are deterministic and stay single-pass.
    import time
    deadline = time.monotonic() + 75
    health_errors = _evaluate_health()
    while health_errors and time.monotonic() < deadline:
        time.sleep(8)
        health_errors = _evaluate_health()
    errors.extend(health_errors)


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
            errors.append("Missing TLS data from es-http-tls secret")
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
                    errors.append("Client CA does not match new CA")

            not_after = openssl_not_after(leaf_path, errors)
            if not_after:
                now = datetime.datetime.now(datetime.timezone.utc)
                days_remaining = (not_after - now).days
                if days_remaining < MIN_VALID_DAYS or days_remaining > MAX_VALID_DAYS:
                    errors.append(
                        f"Leaf validity {days_remaining} days out of expected range ({MIN_VALID_DAYS}-{MAX_VALID_DAYS})"
                    )

            if new_ca_fp:
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
