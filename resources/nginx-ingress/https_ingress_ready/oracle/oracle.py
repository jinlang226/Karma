#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tempfile
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from oracle_lib import (  # noqa: E402
    bench_namespace,
    bench_ns,
    controller_service_host,
    ingress_class_name,
    ingress_paths,
    ingress_tls_secret,
    pod_exec,
    secret,
    secret_data_text,
)


def _cert_valid_for(cert_pem: str, *, host: str, min_validity_seconds: int) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(cert_pem)
        cert_path = handle.name
    from subprocess import run

    check = run(
        ["openssl", "x509", "-in", cert_path, "-noout", "-checkend", str(min_validity_seconds)],
        text=True,
        capture_output=True,
    )
    if check.returncode != 0:
        return False, "certificate expires too soon or is invalid"
    san = run(
        ["openssl", "x509", "-in", cert_path, "-noout", "-ext", "subjectAltName"],
        text=True,
        capture_output=True,
    )
    if san.returncode != 0:
        return False, "failed to read subjectAltName"
    if f"DNS:{host}" not in san.stdout:
        return False, f"certificate SAN does not contain DNS:{host}"
    return True, "certificate is valid"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", required=True, choices=["secret", "ingress", "https"])
    parser.add_argument("--ingress-name", required=True)
    parser.add_argument("--service-name", required=True)
    parser.add_argument("--tls-secret-name", required=True)
    parser.add_argument("--curl-pod-name", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--expected-ingress-class", required=True)
    parser.add_argument("--expected-body", required=True)
    parser.add_argument("--min-validity-seconds", type=int, required=True)
    args = parser.parse_args()

    app_ns = bench_namespace()
    ingress_ns = bench_ns("ingress", "nginx-ingress")

    if args.check == "secret":
        payload = secret(app_ns, args.tls_secret_name)
        secret_type = str(payload.get("type") or "")
        if secret_type != "kubernetes.io/tls":
            print(f"secret/{args.tls_secret_name} type={secret_type!r}, expected 'kubernetes.io/tls'")
            return 1
        cert_pem = secret_data_text(app_ns, args.tls_secret_name, "tls.crt")
        key_pem = secret_data_text(app_ns, args.tls_secret_name, "tls.key")
        if not cert_pem or not key_pem:
            print(f"secret/{args.tls_secret_name} is missing tls.crt or tls.key")
            return 1
        ok, reason = _cert_valid_for(
            cert_pem,
            host=args.host,
            min_validity_seconds=args.min_validity_seconds,
        )
        if not ok:
            print(f"secret/{args.tls_secret_name} invalid: {reason}")
            return 1
        print(f"secret/{args.tls_secret_name} contains a valid certificate for {args.host}")
        return 0

    if args.check == "ingress":
        class_name = ingress_class_name(app_ns, args.ingress_name)
        if class_name != args.expected_ingress_class:
            print(
                f"ingress/{args.ingress_name} ingressClassName={class_name!r}, "
                f"expected {args.expected_ingress_class!r}"
            )
            return 1
        paths = ingress_paths(app_ns, args.ingress_name, host=args.host)
        if (args.host, args.path, args.service_name) not in paths:
            print(
                f"ingress/{args.ingress_name} rules {paths!r} do not include "
                f"({args.host!r}, {args.path!r}, {args.service_name!r})"
            )
            return 1
        tls_secret = ingress_tls_secret(app_ns, args.ingress_name, host=args.host)
        if tls_secret != args.tls_secret_name:
            print(
                f"ingress/{args.ingress_name} TLS secret={tls_secret!r}, "
                f"expected {args.tls_secret_name!r}"
            )
            return 1
        print(f"ingress/{args.ingress_name} terminates TLS with {args.tls_secret_name}")
        return 0

    body = pod_exec(
        app_ns,
        args.curl_pod_name,
        [
            "curl",
            "-k",
            "-sS",
            "--connect-to",
            f"{args.host}:443:{controller_service_host(ingress_ns)}:443",
            f"https://{args.host}{args.path}",
        ],
    ).strip()
    if body != args.expected_body:
        print(f"HTTPS route returned {body!r}, expected {args.expected_body!r}")
        return 1
    print(f"HTTPS route returned {body!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
