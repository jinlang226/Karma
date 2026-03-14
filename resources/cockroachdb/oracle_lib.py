#!/usr/bin/env python3
import json
import os
import subprocess


def run(cmd, timeout=None):
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def bench_namespace(default="cockroachdb"):
    return os.environ.get("BENCH_NAMESPACE", default)


def bench_param(name, default=""):
    return os.environ.get(f"BENCH_PARAM_{name.upper()}", str(default))


def bench_param_int(name, default):
    raw = os.environ.get(f"BENCH_PARAM_{name.upper()}")
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return int(str(raw).strip())
    except ValueError:
        return int(default)


def cluster_prefix(default="crdb-cluster"):
    return bench_param("cluster_prefix", default)


def cluster_pod(prefix, ordinal=0):
    return f"{prefix}-{ordinal}"


def cluster_service(prefix):
    return prefix


def cluster_public_service(prefix):
    return f"{prefix}-public"


def cluster_service_account(prefix):
    return f"{prefix}-sa"


def cluster_sql_host(prefix, namespace, ordinal=0):
    return f"{prefix}-{ordinal}.{prefix}.{namespace}.svc.cluster.local"


def cockroach_image(version):
    version_text = str(version).strip()
    if version_text and not version_text.startswith("v"):
        version_text = f"v{version_text}"
    return f"cockroachdb/cockroach:{version_text}"


def version_family(version):
    clean = str(version).strip().lstrip("v")
    parts = [part for part in clean.split(".") if part != ""]
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return clean


def parse_tsv(output):
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return [], []
    header = lines[0].split("\t")
    rows = [line.split("\t") for line in lines[1:]]
    return header, rows


def tsv_last_value(output):
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    return lines[-1].split("\t")[-1]


def to_bool(value):
    return str(value).strip().lower() in ("true", "t", "1", "yes")


def kubectl_json(namespace, args):
    cmd = ["kubectl", "-n", namespace] + list(args) + ["-o", "json"]
    result = run(cmd)
    if result.returncode != 0:
        return None, result.stderr.strip() or "kubectl failed"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"json decode failed: {exc}"
