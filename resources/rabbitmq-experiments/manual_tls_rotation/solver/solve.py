#!/usr/bin/env python3
import base64
import json
import os
import tempfile
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parents[2] / "common"
sys.path.insert(0, str(COMMON))

from solver_utils import run, kubectl_json, wait_statefulset_ready, wait_until  # noqa: E402


NAMESPACE = os.environ.get("BENCH_NAMESPACE", "rabbitmq")
CLUSTER_PREFIX = os.environ.get("BENCH_PARAM_CLUSTER_PREFIX", "rabbitmq")


def _fp_sha256(path):
    out = run(["openssl", "x509", "-noout", "-fingerprint", "-sha256", "-in", str(path)])
    return out.strip().split("=", 1)[-1]


def _live_leaf_fingerprint():
    out = run(
        [
            "kubectl",
            "-n",
            NAMESPACE,
            "exec",
            "openssl-toolbox",
            "--",
            "/bin/sh",
            "-lc",
            (
                "echo | openssl s_client "
                f"-connect {CLUSTER_PREFIX}.{NAMESPACE}.svc.cluster.local:5671 "
                f"-servername {CLUSTER_PREFIX} 2>/dev/null | "
                "openssl x509 -noout -fingerprint -sha256"
            ),
        ]
    )
    return out.strip().split("=", 1)[-1]


def main():
    tls_secret = f"{CLUSTER_PREFIX}-tls"
    sec = kubectl_json("-n", NAMESPACE, "get", "secret", tls_secret)
    ca_b64 = ((sec.get("data") or {}).get("ca.crt") or "").strip()
    if not ca_b64:
        raise RuntimeError(f"{tls_secret} missing ca.crt")
    ca_pem = base64.b64decode(ca_b64)

    old_leaf = None
    try:
        old_cm = f"{CLUSTER_PREFIX}-tls-old"
        cm = kubectl_json("-n", NAMESPACE, "get", "configmap", old_cm)
        old_leaf = ((cm.get("data") or {}).get("leaf_fingerprint_sha256") or "").strip() or None
    except Exception:
        old_leaf = None

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        ca_path = td_path / "ca.crt"
        key_path = td_path / "tls.key"
        csr_path = td_path / "tls.csr"
        crt_path = td_path / "tls.crt"

        ca_path.write_bytes(ca_pem)

        run(["openssl", "genrsa", "-out", str(key_path), "2048"])
        run(
            [
                "openssl",
                "req",
                "-new",
                "-key",
                str(key_path),
                "-subj",
                "/CN=rabbitmq",
                "-out",
                str(csr_path),
            ]
        )
        run(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(csr_path),
                "-signkey",
                str(key_path),
                "-out",
                str(crt_path),
                "-days",
                "365",
                "-sha256",
            ]
        )

        if old_leaf and _fp_sha256(crt_path) == old_leaf:
            raise RuntimeError("generated leaf fingerprint did not rotate")

        manifest = run(
            [
                "kubectl",
                "-n",
                NAMESPACE,
                "create",
                "secret",
                "generic",
                tls_secret,
                f"--from-file=ca.crt={ca_path}",
                f"--from-file=tls.crt={crt_path}",
                f"--from-file=tls.key={key_path}",
                "--dry-run=client",
                "-o",
                "yaml",
            ]
        )

    run(["kubectl", "-n", NAMESPACE, "apply", "-f", "-"], input_text=manifest)
    run(["kubectl", "-n", NAMESPACE, "rollout", "restart", f"statefulset/{CLUSTER_PREFIX}"])
    wait_statefulset_ready(NAMESPACE, CLUSTER_PREFIX, timeout_sec=900)

    secret_after = kubectl_json("-n", NAMESPACE, "get", "secret", tls_secret)
    leaf_bytes = base64.b64decode(((secret_after.get("data") or {}).get("tls.crt") or "").strip())
    with tempfile.NamedTemporaryFile("wb", delete=False) as tf:
        tf.write(leaf_bytes)
        leaf_file = Path(tf.name)
    secret_leaf_fp = _fp_sha256(leaf_file)

    wait_until(
        lambda: _live_leaf_fingerprint() == secret_leaf_fp,
        timeout_sec=180,
        interval_sec=5,
        description="live TLS leaf to match secret",
    )
    print("manual_tls_rotation solver applied")


if __name__ == "__main__":
    main()
