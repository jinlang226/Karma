#!/usr/bin/env python3
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_param,
    cluster_pod,
    cluster_prefix,
    kubectl_json,
    run,
)


def main():
    namespace = bench_namespace("cockroachdb")
    prefix = cluster_prefix("crdb-cluster")
    pod0 = cluster_pod(prefix, 0)
    cert_secret_name = bench_param("cert_secret_name", "crdb-cluster-certs")

    errors = []

    secret, secret_err = kubectl_json(namespace, ["get", "secret", cert_secret_name])
    if secret_err:
        errors.append(f"Certificate secret '{cert_secret_name}' not found: {secret_err}")
    else:
        data = secret.get("data") or {}
        for required_key in ("ca.crt", "node.crt", "node.key"):
            if required_key not in data:
                errors.append(f"Certificate secret missing key: {required_key}")

    insecure_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        pod0,
        "--",
        "./cockroach",
        "sql",
        "--insecure",
        "-e",
        "SELECT 1;",
    ]
    insecure_result = run(insecure_cmd)
    if insecure_result.returncode == 0:
        errors.append("Insecure SQL connection unexpectedly succeeded")

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
        errors.append("Secure SQL connection failed")
        errors.append(f"Error: {secure_result.stderr.strip()}")

    if errors:
        print("Certificate generation verification failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Certificates generated successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
