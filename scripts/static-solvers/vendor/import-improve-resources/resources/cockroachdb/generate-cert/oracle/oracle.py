#!/usr/bin/env python3
import base64
import datetime
import json
import subprocess
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
    replica_count = int(bench_param("replica_count", "3"))
    validity_days = int(bench_param("cert_validity_days", "365"))

    errors = []

    secret, secret_err = kubectl_json(namespace, ["get", "secret", cert_secret_name])
    if secret_err:
        errors.append(f"Certificate secret '{cert_secret_name}' not found: {secret_err}")
    else:
        data = secret.get("data") or {}
        for required_key in ("ca.crt", "node.crt", "node.key"):
            if required_key not in data:
                errors.append(f"Certificate secret missing key: {required_key}")
        node_crt = data.get("node.crt")
        if node_crt:
            cert_result = subprocess.run(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "exec",
                    "-i",
                    "openssl-toolbox",
                    "--",
                    "openssl",
                    "x509",
                    "-noout",
                    "-enddate",
                ],
                input=base64.b64decode(node_crt),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if cert_result.returncode != 0:
                errors.append("Unable to inspect generated node certificate validity")
            else:
                not_after = cert_result.stdout.decode().strip().removeprefix("notAfter=")
                try:
                    expires = datetime.datetime.strptime(
                        not_after, "%b %d %H:%M:%S %Y %Z"
                    ).replace(tzinfo=datetime.timezone.utc)
                    days_left = (
                        expires - datetime.datetime.now(datetime.timezone.utc)
                    ).days
                    if days_left < validity_days - 2 or days_left > validity_days + 2:
                        errors.append(
                            "Node certificate validity does not match "
                            f"{validity_days} days: {days_left} days remain"
                        )
                except ValueError:
                    errors.append(f"Unable to parse node certificate expiry: {not_after}")

    statefulset, statefulset_err = kubectl_json(
        namespace, ["get", "statefulset", prefix]
    )
    if statefulset_err:
        errors.append(f"Unable to inspect StatefulSet topology: {statefulset_err}")
    else:
        spec_replicas = (statefulset.get("spec") or {}).get("replicas")
        ready_replicas = (statefulset.get("status") or {}).get("readyReplicas", 0)
        if spec_replicas != replica_count:
            errors.append(
                f"StatefulSet replicas expected {replica_count}, got {spec_replicas}"
            )
        if ready_replicas != replica_count:
            errors.append(
                f"Ready replicas expected {replica_count}, got {ready_replicas}"
            )

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
