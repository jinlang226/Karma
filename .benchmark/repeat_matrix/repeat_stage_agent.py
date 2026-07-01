#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import tempfile
import time
import traceback
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SUBMIT_FILE = Path(os.environ.get("BENCHMARK_SUBMIT_FILE", "submit.signal"))
WORKSPACE_ROOT = SUBMIT_FILE.resolve().parent if SUBMIT_FILE.is_absolute() else Path.cwd()
PROMPT_PATH = WORKSPACE_ROOT / "PROMPT.md"
SUBMIT_ACK = WORKSPACE_ROOT / "submit.ack"
SUBMIT_RESULT = Path(
    os.environ.get("BENCHMARK_SUBMIT_RESULT_FILE", str(WORKSPACE_ROOT / "submit_result.json"))
)


def _default_host_kubeconfig() -> Path | None:
    candidate = (Path.home() / ".kube" / "config").resolve()
    if candidate.exists():
        return candidate
    return None


def _normalize_kubeconfig() -> None:
    raw = (os.environ.get("KUBECONFIG") or "").strip()
    if raw:
        candidate = Path(raw)
        if candidate.is_absolute() and candidate.exists():
            if candidate.name == "kubeconfig-proxy":
                direct = _default_host_kubeconfig()
                if direct is not None:
                    os.environ["KUBECONFIG"] = str(direct)
                    return
            return
        if not candidate.is_absolute():
            direct = (Path.cwd() / candidate).resolve()
            if direct.exists():
                if direct.name == "kubeconfig-proxy":
                    host_kubeconfig = _default_host_kubeconfig()
                    if host_kubeconfig is not None:
                        os.environ["KUBECONFIG"] = str(host_kubeconfig)
                        return
                os.environ["KUBECONFIG"] = str(direct)
                return
            workspace_candidate = (WORKSPACE_ROOT / candidate).resolve()
            if workspace_candidate.exists():
                if workspace_candidate.name == "kubeconfig-proxy":
                    host_kubeconfig = _default_host_kubeconfig()
                    if host_kubeconfig is not None:
                        os.environ["KUBECONFIG"] = str(host_kubeconfig)
                        return
                os.environ["KUBECONFIG"] = str(workspace_candidate)
                return
    default_candidate = (WORKSPACE_ROOT / "kubeconfig-proxy").resolve()
    if default_candidate.exists():
        host_kubeconfig = _default_host_kubeconfig()
        if host_kubeconfig is not None:
            os.environ["KUBECONFIG"] = str(host_kubeconfig)
            return
        os.environ["KUBECONFIG"] = str(default_candidate)


_normalize_kubeconfig()


def _command_with_kubeconfig(cmd: list[str]) -> list[str]:
    if not cmd or cmd[0] != "kubectl":
        return cmd
    for part in cmd[1:]:
        text = str(part)
        if text == "--kubeconfig" or text.startswith("--kubeconfig="):
            return cmd
    kubeconfig = (os.environ.get("KUBECONFIG") or "").strip()
    if not kubeconfig:
        return cmd
    return ["kubectl", f"--kubeconfig={kubeconfig}", *cmd[1:]]


def _proc(cmd: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    cmd = _command_with_kubeconfig(cmd)
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
        stdout=None,
        stderr=None,
        check=False,
    )


def run(cmd: list[str], *, input_text: str | None = None) -> None:
    proc = _proc(cmd, input_text=input_text)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def run_ok(cmd: list[str], *, input_text: str | None = None) -> bool:
    return _proc(cmd, input_text=input_text).returncode == 0


def capture(cmd: list[str], *, input_text: str | None = None) -> str:
    cmd = _command_with_kubeconfig(cmd)
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)
    return proc.stdout


def capture_maybe(cmd: list[str], *, input_text: str | None = None) -> str | None:
    cmd = _command_with_kubeconfig(cmd)
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def service_cluster_ip(namespace: str, name: str) -> str:
    cluster_ip = capture(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "service",
            name,
            "-o",
            "jsonpath={.spec.clusterIP}",
        ]
    ).strip()
    if not cluster_ip or cluster_ip.lower() == "none":
        raise RuntimeError(f"service/{name} has no routable cluster IP")
    return cluster_ip


def env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required env var: {name}")
    return value


def active_stage_id() -> str:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    match = re.search(r"Active Stage:\s*\d+/\d+\s*\(([^)]+)\)", text)
    if not match:
        raise RuntimeError("cannot parse active stage from PROMPT.md")
    return match.group(1).strip()


def submit_ack_stage_id() -> str | None:
    if not SUBMIT_ACK.exists():
        return None
    try:
        payload = json.loads(SUBMIT_ACK.read_text(encoding="utf-8"))
    except Exception:
        return None
    stage_id = str(payload.get("stage_id") or "").strip()
    return stage_id or None


def apply_template(relative_path: str, replacements: dict[str, str], *, namespace: str | None = None) -> None:
    raw = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    for old, new in replacements.items():
        raw = raw.replace(old, new)
    cmd = ["kubectl"]
    if namespace:
        cmd.extend(["-n", namespace])
    cmd.extend(["apply", "--validate=false", "-f", "-"])
    run(cmd, input_text=raw)


_ENV_TEMPLATE_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def render_env_template(raw: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        return os.environ.get(name, match.group(0))

    return _ENV_TEMPLATE_RE.sub(replace, raw)


def apply_env_template(relative_path: str, *, namespace: str | None = None) -> None:
    raw = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    raw = render_env_template(raw)
    cmd = ["kubectl"]
    if namespace:
        cmd.extend(["-n", namespace])
    cmd.extend(["apply", "--validate=false", "-f", "-"])
    run(cmd, input_text=raw)


def wait_job(namespace: str, job_name: str, *, timeout: str = "300s") -> None:
    timeout_sec = int(timeout[:-1] if timeout.endswith("s") else timeout)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        payload = capture(
            [
                "kubectl",
                "-n",
                namespace,
                "get",
                "job",
                job_name,
                "-o",
                "jsonpath={.status.succeeded} {.status.failed}",
            ]
        ).strip()
        parts = payload.split()
        succeeded = int(parts[0]) if len(parts) >= 1 and parts[0] else 0
        failed = int(parts[1]) if len(parts) >= 2 and parts[1] else 0
        if succeeded >= 1:
            return
        if failed >= 1:
            raise RuntimeError(f"job/{job_name} failed")
        time.sleep(2)
    raise RuntimeError(f"job/{job_name} did not complete within {timeout}")


def rollout_status(namespace: str, resource: str, *, timeout: str = "180s") -> None:
    run(["kubectl", "-n", namespace, "rollout", "status", resource, f"--timeout={timeout}"])


def wait_until(check_fn, *, timeout_sec: int = 90, interval_sec: int = 2, err: str) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if check_fn():
            return
        time.sleep(interval_sec)
    raise RuntimeError(err)


def namespace_alias(role: str) -> str:
    return env_required(f"BENCH_NS_{role.upper()}")


def solve_noop() -> None:
    return


def run_case_solver(relative_path: str) -> None:
    run(["python3", relative_path])


def apply_literal_manifest(
    relative_path: str,
    replacements: dict[str, str],
    *,
    namespace: str | None = None,
) -> None:
    raw = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
    for old, new in replacements.items():
        raw = raw.replace(old, new)
    cmd = ["kubectl"]
    if namespace:
        cmd.extend(["-n", namespace])
    cmd.extend(["apply", "--validate=false", "-f", "-"])
    run(cmd, input_text=raw)


def create_or_apply_configmap(namespace: str, name: str, literals: dict[str, str]) -> None:
    cmd = ["kubectl", "-n", namespace, "create", "configmap", name]
    for key, value in literals.items():
        cmd.append(f"--from-literal={key}={value}")
    cmd.extend(["--dry-run=client", "-o", "yaml"])
    payload = capture(cmd)
    run(["kubectl", "-n", namespace, "apply", "--validate=false", "-f", "-"], input_text=payload)


def create_or_apply_secret(namespace: str, name: str, files: dict[str, Path]) -> None:
    cmd = ["kubectl", "-n", namespace, "create", "secret", "generic", name]
    for key, path in files.items():
        cmd.append(f"--from-file={key}={path}")
    cmd.extend(["--dry-run=client", "-o", "yaml"])
    payload = capture(cmd)
    run(["kubectl", "-n", namespace, "apply", "--validate=false", "-f", "-"], input_text=payload)


def patch_json(namespace: str, resource: str, patch_ops: list[dict]) -> None:
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "patch",
            resource,
            "--type=json",
            "-p",
            json.dumps(patch_ops),
        ]
    )


def replace_json(namespace: str, resource: str, patch_payload: dict) -> None:
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "patch",
            resource,
            "--type=merge",
            "-p",
            json.dumps(patch_payload),
        ]
    )


def set_workload_container_image(namespace: str, resource: str, container: str, image: str) -> None:
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "set",
            "image",
            resource,
            f"{container}={image}",
        ]
    )


def statefulset_ready_replicas(namespace: str, name: str) -> int:
    raw = capture(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "sts",
            name,
            "-o",
            "jsonpath={.status.readyReplicas}",
        ]
    ).strip()
    return int(raw or "0")


def wait_statefulset_ready(namespace: str, name: str, expected: int, *, timeout_sec: int = 600) -> None:
    wait_until(
        lambda: statefulset_ready_replicas(namespace, name) == expected,
        timeout_sec=timeout_sec,
        interval_sec=5,
        err=f"statefulset/{name} did not reach {expected} ready replicas",
    )


def pod_phase(namespace: str, name: str) -> str:
    return capture(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "pod",
            name,
            "-o",
            "jsonpath={.status.phase}",
        ]
    ).strip()


def wait_cockroach_pods_running(namespace: str, prefix: str, replicas: int, *, timeout_sec: int = 600) -> None:
    def all_running() -> bool:
        for ordinal in range(replicas):
            if pod_phase(namespace, cockroach_pod(prefix, ordinal)) != "Running":
                return False
        return True

    wait_until(
        all_running,
        timeout_sec=timeout_sec,
        interval_sec=5,
        err=f"cockroach pods for statefulset/{prefix} did not reach Running phase",
    )


def wait_cockroach_statefulset_revision(namespace: str, prefix: str, replicas: int, *, timeout_sec: int = 900) -> None:
    def all_current_revision_ready() -> bool:
        sts = json.loads(
            capture(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "get",
                    "sts",
                    prefix,
                    "-o",
                    "json",
                ]
            )
        )
        desired_revision = (
            (sts.get("status") or {}).get("updateRevision")
            or (sts.get("status") or {}).get("currentRevision")
            or ""
        )
        if not desired_revision:
            return False
        pods = json.loads(
            capture(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "get",
                    "pods",
                    "-l",
                    "app.kubernetes.io/name=cockroachdb",
                    "-o",
                    "json",
                ]
            )
        )
        by_name = {
            (item.get("metadata") or {}).get("name", ""): item for item in (pods.get("items") or [])
        }
        for ordinal in range(replicas):
            item = by_name.get(cockroach_pod(prefix, ordinal))
            if not item:
                return False
            labels = (item.get("metadata") or {}).get("labels") or {}
            if labels.get("controller-revision-hash") != desired_revision:
                return False
            if (item.get("status") or {}).get("phase") != "Running":
                return False
            statuses = (item.get("status") or {}).get("containerStatuses") or []
            if not statuses or not all(status.get("ready") for status in statuses):
                return False
        return True

    wait_until(
        all_current_revision_ready,
        timeout_sec=timeout_sec,
        interval_sec=5,
        err=f"cockroach pods for statefulset/{prefix} did not converge to the current revision",
    )


def env_param(name: str, default: str) -> str:
    value = os.environ.get(f"BENCH_PARAM_{name.upper()}")
    if value is None or not value.strip():
        return default
    return value.strip()


def cockroach_prefix() -> str:
    return env_param("cluster_prefix", "crdb-cluster")


def cockroach_image_ref(version: str) -> str:
    clean = str(version).strip()
    if clean and not clean.startswith("v"):
        clean = f"v{clean}"
    return f"cockroachdb/cockroach:{clean}"


def cockroach_pod(prefix: str, ordinal: int = 0) -> str:
    return f"{prefix}-{ordinal}"


def cockroach_host(prefix: str, namespace: str, ordinal: int = 0) -> str:
    return f"{prefix}-{ordinal}.{prefix}.{namespace}.svc.cluster.local"


def cockroach_replacements(namespace: str) -> dict[str, str]:
    prefix = cockroach_prefix()
    monitoring_ns = os.environ.get("BENCH_NS_MONITORING", "monitoring")
    ingress_ns = os.environ.get("BENCH_NS_INGRESS", "ingress-nginx")
    replica_count = env_param("replica_count", "3")
    storage_size_gi = env_param("storage_size_gi", "10")
    to_version = env_param("to_version", "24.1.0")
    return {
        "namespace: cockroachdb": f"namespace: {namespace}",
        "namespace: monitoring": f"namespace: {monitoring_ns}",
        "namespace: ingress-nginx": f"namespace: {ingress_ns}",
        "crdb-cluster": prefix,
        "cockroachdb/cockroach:v24.1.0": f"cockroachdb/cockroach:v{to_version}",
        "cockroachdb/cockroach:v24.1.1": f"cockroachdb/cockroach:v{env_param('to_version', '24.1.1')}",
        "cockroachdb/cockroach:v23.2.0": f"cockroachdb/cockroach:v{env_param('from_version', '23.2.0')}",
        "__REPLICA_COUNT__": replica_count,
        "__STORAGE_SIZE_GI__": storage_size_gi,
        "__COCKROACH_VERSION__": to_version,
    }


def apply_cockroach_manifest(relative_path: str, namespace: str) -> None:
    apply_env_template(relative_path, namespace=namespace)


def cockroach_exec(namespace: str, prefix: str, args: list[str], *, ordinal: int = 0) -> subprocess.CompletedProcess[str]:
    return _proc(["kubectl", "-n", namespace, "exec", cockroach_pod(prefix, ordinal), "--"] + args)


def cockroach_capture(namespace: str, prefix: str, args: list[str], *, ordinal: int = 0) -> str:
    return capture(["kubectl", "-n", namespace, "exec", cockroach_pod(prefix, ordinal), "--"] + args)


def cockroach_init_insecure(namespace: str, prefix: str) -> None:
    host = cockroach_host(prefix, namespace, 0)
    wait_until(
        lambda: run_ok(
            [
                "kubectl",
                "-n",
                namespace,
                "exec",
                cockroach_pod(prefix, 0),
                "--",
                "./cockroach",
                "init",
                "--insecure",
                f"--host={host}",
            ]
        ),
        timeout_sec=120,
        interval_sec=3,
        err="cockroach init did not succeed",
    )


def cockroach_wait_sql(namespace: str, prefix: str, *, secure: bool = False) -> None:
    host = cockroach_host(prefix, namespace, 0)
    cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        cockroach_pod(prefix, 0),
        "--",
        "./cockroach",
        "sql",
    ]
    if secure:
        cmd.append("--certs-dir=/cockroach/cockroach-certs")
    else:
        cmd.append("--insecure")
    cmd.extend([f"--host={host}", "-e", "SELECT 1;"])
    wait_until(
        lambda: run_ok(cmd),
        timeout_sec=180,
        interval_sec=5,
        err="cockroach SQL did not become ready",
    )


def cockroach_sql(namespace: str, prefix: str, sql: str, *, secure: bool = False, ordinal: int = 0) -> str:
    cmd = ["./cockroach", "sql"]
    if secure:
        cmd.append("--certs-dir=/cockroach/cockroach-certs")
    else:
        cmd.append("--insecure")
    cmd.extend(["--format=tsv", "-e", sql])
    return cockroach_capture(namespace, prefix, cmd, ordinal=ordinal)


def ensure_cockroach_insecure_cluster(namespace: str, resource_dir: str, *, replicas: int = 3) -> None:
    prefix = cockroach_prefix()
    apply_cockroach_manifest(f"{resource_dir}/rbac.yaml", namespace)
    apply_cockroach_manifest(f"{resource_dir}/services.yaml", namespace)
    apply_cockroach_manifest(f"{resource_dir}/statefulset.yaml", namespace)
    rollout_status(namespace, f"statefulset/{prefix}", timeout="900s")
    wait_statefulset_ready(namespace, prefix, replicas, timeout_sec=900)
    cockroach_init_insecure(namespace, prefix)
    cockroach_wait_sql(namespace, prefix, secure=False)


def generate_cockroach_tls_material(namespace: str, prefix: str, days: str, *, reuse_ca: dict[str, bytes] | None = None) -> dict[str, bytes]:
    hostnames = [
        "DNS:localhost",
        "IP:127.0.0.1",
        f"DNS:{prefix}",
        f"DNS:{prefix}.{namespace}",
        f"DNS:{prefix}.{namespace}.svc",
        f"DNS:{prefix}.{namespace}.svc.cluster.local",
        f"DNS:*.{prefix}.{namespace}.svc.cluster.local",
        f"DNS:{prefix}-0.{prefix}.{namespace}.svc.cluster.local",
        f"DNS:{prefix}-1.{prefix}.{namespace}.svc.cluster.local",
        f"DNS:{prefix}-2.{prefix}.{namespace}.svc.cluster.local",
    ]
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        if reuse_ca:
            for name, payload in reuse_ca.items():
                (td_path / name).write_bytes(payload)
        else:
            run(
                [
                    "openssl",
                    "genrsa",
                    "-out",
                    str(td_path / "ca.key"),
                    "2048",
                ]
            )
            run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-new",
                    "-nodes",
                    "-key",
                    str(td_path / "ca.key"),
                    "-subj",
                    "/CN=CockroachDB CA",
                    "-days",
                    "3650",
                    "-out",
                    str(td_path / "ca.crt"),
                ]
            )
        run(["openssl", "genrsa", "-out", str(td_path / "node.key"), "2048"])
        run(
            [
                "openssl",
                "req",
                "-new",
                "-key",
                str(td_path / "node.key"),
                "-subj",
                "/CN=node",
                "-out",
                str(td_path / "node.csr"),
                "-addext",
                f"subjectAltName={','.join(hostnames)}",
            ]
        )
        extfile = td_path / "node.ext"
        extfile.write_text(
            "\n".join(
                [
                    "subjectAltName=" + ",".join(hostnames),
                    "keyUsage=critical,digitalSignature,keyEncipherment",
                    "extendedKeyUsage=serverAuth,clientAuth",
                ]
            ),
            encoding="utf-8",
        )
        run(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(td_path / "node.csr"),
                "-CA",
                str(td_path / "ca.crt"),
                "-CAkey",
                str(td_path / "ca.key"),
                "-CAcreateserial",
                "-out",
                str(td_path / "node.crt"),
                "-days",
                str(days),
                "-extfile",
                str(extfile),
            ]
        )
        if not reuse_ca:
            run(["openssl", "genrsa", "-out", str(td_path / "client.root.key"), "2048"])
            run(
                [
                    "openssl",
                    "req",
                    "-new",
                    "-key",
                    str(td_path / "client.root.key"),
                    "-subj",
                    "/CN=root",
                    "-out",
                    str(td_path / "client.root.csr"),
                ]
            )
            run(
                [
                    "openssl",
                    "x509",
                    "-req",
                    "-in",
                    str(td_path / "client.root.csr"),
                    "-CA",
                    str(td_path / "ca.crt"),
                    "-CAkey",
                    str(td_path / "ca.key"),
                    "-CAcreateserial",
                    "-out",
                    str(td_path / "client.root.crt"),
                    "-days",
                    str(days),
                ]
            )
        result = {}
        for name in ("ca.crt", "ca.key", "node.crt", "node.key", "client.root.crt", "client.root.key"):
            path = td_path / name
            if path.exists():
                result[name] = path.read_bytes()
        return result


def apply_cockroach_tls_secret(namespace: str, secret_name: str, material: dict[str, bytes]) -> None:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        files = {}
        for name, payload in material.items():
            if name == "ca.key":
                continue
            path = td_path / name
            path.write_bytes(payload)
            files[name] = path
        create_or_apply_secret(namespace, secret_name, files)


def decode_secret_data(namespace: str, name: str) -> dict[str, bytes]:
    payload = json.loads(
        capture(["kubectl", "-n", namespace, "get", "secret", name, "-o", "json"])
    )
    data = {}
    for key, raw in (payload.get("data") or {}).items():
        data[key] = base64.b64decode(raw)
    return data


def solve_demo_configmap_update() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    target_value = env_required("BENCH_PARAM_TARGET_VALUE")
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "patch",
            "configmap",
            "demo-config",
            "--type=merge",
            "-p",
            json.dumps({"data": {"value": target_value}}),
        ]
    )


def solve_demo_configmap_update_two_ns() -> None:
    source_ns = namespace_alias("source")
    target_ns = namespace_alias("target")
    source_value = env_required("BENCH_PARAM_SOURCE_VALUE")
    target_value = env_required("BENCH_PARAM_TARGET_VALUE")
    run(
        [
            "kubectl",
            "-n",
            source_ns,
            "patch",
            "configmap",
            "demo-config",
            "--type=merge",
            "-p",
            json.dumps({"data": {"value": source_value}}),
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            target_ns,
            "patch",
            "configmap",
            "demo-config",
            "--type=merge",
            "-p",
            json.dumps({"data": {"value": target_value}}),
        ]
    )


def solve_spark_pi() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    apply_template(
        "resources/spark/spark_pi_job_execution/resource/spark-pi-job.yaml",
        {
            "__NAMESPACE__": namespace,
            "__JOB_NAME__": env_required("BENCH_PARAM_JOB_NAME"),
            "__SERVICE_ACCOUNT__": env_required("BENCH_PARAM_SERVICE_ACCOUNT_NAME"),
            "__SPARK_IMAGE__": env_required("BENCH_PARAM_SPARK_IMAGE"),
            "__DRIVER_MEMORY__": env_required("BENCH_PARAM_DRIVER_MEMORY"),
            "__ITERATIONS__": env_required("BENCH_PARAM_ITERATIONS"),
        },
    )
    wait_job(namespace, env_required("BENCH_PARAM_JOB_NAME"), timeout="180s")


def solve_spark_sql() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    apply_template(
        "resources/spark/spark_sql_job_execution/resource/sql-job.yaml",
        {
            "__NAMESPACE__": namespace,
            "__JOB_NAME__": env_required("BENCH_PARAM_JOB_NAME"),
            "__SERVICE_ACCOUNT__": env_required("BENCH_PARAM_SERVICE_ACCOUNT_NAME"),
            "__SPARK_IMAGE__": env_required("BENCH_PARAM_SPARK_IMAGE"),
        },
    )
    wait_job(namespace, env_required("BENCH_PARAM_JOB_NAME"), timeout="300s")


def solve_spark_etl() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    apply_template(
        "resources/spark/spark_etl_pipeline_completion/resource/etl-storage.yaml",
        {
            "__NAMESPACE__": namespace,
            "__PVC_NAME__": env_required("BENCH_PARAM_PVC_NAME"),
        },
    )
    apply_template(
        "resources/spark/spark_etl_pipeline_completion/resource/etl-job.yaml",
        {
            "__NAMESPACE__": namespace,
            "__JOB_NAME__": env_required("BENCH_PARAM_JOB_NAME"),
            "__SERVICE_ACCOUNT__": env_required("BENCH_PARAM_SERVICE_ACCOUNT_NAME"),
            "__PVC_NAME__": env_required("BENCH_PARAM_PVC_NAME"),
            "__SPARK_IMAGE__": env_required("BENCH_PARAM_SPARK_IMAGE"),
            "__DATA_MOUNT__": env_required("BENCH_PARAM_DATA_MOUNT"),
        },
    )
    wait_job(namespace, env_required("BENCH_PARAM_JOB_NAME"), timeout="300s")


def solve_spark_runtime_bundle() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    apply_template(
        "resources/spark/spark_runtime_bundle_ready/resource/spark-config.yaml",
        {
            "__NAMESPACE__": namespace,
            "__CONFIGMAP_NAME__": env_required("BENCH_PARAM_CONFIGMAP_NAME"),
            "__EXECUTOR_MEMORY__": env_required("BENCH_PARAM_EXECUTOR_MEMORY"),
            "__DRIVER_MEMORY__": env_required("BENCH_PARAM_DRIVER_MEMORY"),
        },
    )
    apply_template(
        "resources/spark/spark_runtime_bundle_ready/resource/spark-credentials.yaml",
        {
            "__NAMESPACE__": namespace,
            "__SECRET_NAME__": env_required("BENCH_PARAM_SECRET_NAME"),
            "__API_KEY__": env_required("BENCH_PARAM_API_KEY"),
        },
    )
    apply_template(
        "resources/spark/spark_runtime_bundle_ready/resource/spark-monitor.yaml",
        {
            "__NAMESPACE__": namespace,
            "__DEPLOYMENT_NAME__": env_required("BENCH_PARAM_MONITOR_DEPLOYMENT_NAME"),
            "__SERVICE_ACCOUNT__": env_required("BENCH_PARAM_SERVICE_ACCOUNT_NAME"),
            "__SPARK_IMAGE__": env_required("BENCH_PARAM_SPARK_IMAGE"),
        },
    )
    rollout_status(namespace, f"deployment/{env_required('BENCH_PARAM_MONITOR_DEPLOYMENT_NAME')}", timeout="180s")
    apply_template(
        "resources/spark/spark_runtime_bundle_ready/resource/spark-batch-job.yaml",
        {
            "__NAMESPACE__": namespace,
            "__JOB_NAME__": env_required("BENCH_PARAM_JOB_NAME"),
            "__SERVICE_ACCOUNT__": env_required("BENCH_PARAM_SERVICE_ACCOUNT_NAME"),
            "__SPARK_IMAGE__": env_required("BENCH_PARAM_SPARK_IMAGE"),
            "__CONFIGMAP_NAME__": env_required("BENCH_PARAM_CONFIGMAP_NAME"),
            "__SECRET_NAME__": env_required("BENCH_PARAM_SECRET_NAME"),
        },
    )
    wait_job(namespace, env_required("BENCH_PARAM_JOB_NAME"), timeout="300s")


def solve_spark_history() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    apply_template(
        "resources/spark/spark_history_server_ready/resource/history-storage.yaml",
        {
            "__NAMESPACE__": namespace,
            "__PVC_NAME__": env_required("BENCH_PARAM_PVC_NAME"),
        },
    )
    apply_template(
        "resources/spark/spark_history_server_ready/resource/history-service.yaml",
        {
            "__NAMESPACE__": namespace,
            "__SERVICE_NAME__": env_required("BENCH_PARAM_SERVICE_NAME"),
            "__DEPLOYMENT_NAME__": env_required("BENCH_PARAM_DEPLOYMENT_NAME"),
            "__SERVICE_PORT__": env_required("BENCH_PARAM_SERVICE_PORT"),
        },
    )
    apply_template(
        "resources/spark/spark_history_server_ready/resource/history-deployment.yaml",
        {
            "__NAMESPACE__": namespace,
            "__DEPLOYMENT_NAME__": env_required("BENCH_PARAM_DEPLOYMENT_NAME"),
            "__SERVICE_ACCOUNT__": env_required("BENCH_PARAM_SERVICE_ACCOUNT_NAME"),
            "__SPARK_IMAGE__": env_required("BENCH_PARAM_SPARK_IMAGE"),
            "__LOG_DIR__": env_required("BENCH_PARAM_LOG_DIR"),
            "__PVC_NAME__": env_required("BENCH_PARAM_PVC_NAME"),
            "__SERVICE_PORT__": env_required("BENCH_PARAM_SERVICE_PORT"),
            "__SERVER_REPLICAS__": env_required("BENCH_PARAM_SERVER_REPLICAS"),
        },
    )
    rollout_status(namespace, f"deployment/{env_required('BENCH_PARAM_DEPLOYMENT_NAME')}", timeout="300s")


def solve_spark_worker_scale() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    cluster_prefix = env_required("BENCH_PARAM_CLUSTER_PREFIX")
    target = env_required("BENCH_PARAM_TARGET_WORKER_REPLICAS")
    run(["kubectl", "-n", namespace, "scale", f"deployment/{cluster_prefix}-worker", f"--replicas={target}"])
    rollout_status(namespace, f"deployment/{cluster_prefix}-worker", timeout="180s")


def solve_spark_multi_tenant() -> None:
    tenant_count = int(env_required("BENCH_PARAM_TENANT_COUNT"))
    job_prefix = env_required("BENCH_PARAM_JOB_NAME_PREFIX")
    sa_prefix = env_required("BENCH_PARAM_SERVICE_ACCOUNT_PREFIX")
    spark_image = env_required("BENCH_PARAM_SPARK_IMAGE")
    driver_memory = env_required("BENCH_PARAM_DRIVER_MEMORY")
    iterations = env_required("BENCH_PARAM_ITERATIONS")
    tenant_specs = [
        ("TEAM_A", "a", "team-a"),
        ("TEAM_B", "b", "team-b"),
        ("TEAM_C", "c", "team-c"),
        ("TEAM_D", "d", "team-d"),
    ]
    for idx, (role, suffix, label) in enumerate(tenant_specs, start=1):
        if idx > tenant_count:
            break
        namespace = namespace_alias(role)
        apply_template(
            "resources/spark/spark_multi_tenant_job_execution/resource/spark-pi-job.yaml",
            {
                "__NAMESPACE__": namespace,
                "__JOB_NAME__": f"{job_prefix}-{suffix}",
                "__SERVICE_ACCOUNT__": f"{sa_prefix}-{suffix}",
                "__SPARK_IMAGE__": spark_image,
                "__DRIVER_MEMORY__": driver_memory,
                "__ITERATIONS__": iterations,
                "__TENANT_LABEL__": label,
            },
        )
        wait_job(namespace, f"{job_prefix}-{suffix}", timeout="300s")


def solve_ray_dashboard() -> None:
    cluster_prefix = env_required("BENCH_PARAM_CLUSTER_PREFIX")
    dashboard_port = env_required("BENCH_PARAM_DASHBOARD_PORT")
    namespace = env_required("BENCH_NAMESPACE")
    service_yaml = f"""apiVersion: v1
kind: Service
metadata:
  name: {cluster_prefix}-head
spec:
  selector:
    app.kubernetes.io/name: ray
    app.kubernetes.io/component: head
    bench.ray.cluster: {cluster_prefix}
  ports:
  - name: gcs
    port: 6379
    targetPort: 6379
  - name: dashboard
    port: {dashboard_port}
    targetPort: 8265
"""
    run(["kubectl", "-n", namespace, "apply", "--validate=false", "-f", "-"], input_text=service_yaml)
    cluster_ip = capture(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "service",
            f"{cluster_prefix}-head",
            "-o",
            "jsonpath={.spec.clusterIP}",
        ]
    ).strip()
    if not cluster_ip or cluster_ip.lower() == "none":
        raise RuntimeError(f"{cluster_prefix}-head has no cluster IP")
    curl_cmd = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        f"{cluster_prefix}-curl-test",
        "--",
        "sh",
        "-c",
        (
            "code=$(curl -sS -o /dev/null -w \"%{http_code}\" "
            f"http://{cluster_ip}:{dashboard_port}/api/cluster_status || true); "
            "[ \"$code\" = \"200\" ]"
        ),
    ]
    wait_until(
        lambda: run_ok(curl_cmd),
        timeout_sec=90,
        interval_sec=2,
        err="dashboard endpoint did not become reachable on port 8265",
    )


def solve_ray_job_execution() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    cluster_prefix = env_required("BENCH_PARAM_CLUSTER_PREFIX")
    for attempt in range(1, 4):
        apply_template(
            "resources/ray/job_execution/resource/ray-job-runner.yaml",
            {
                "__CLUSTER_PREFIX__": cluster_prefix,
                "__KUBECTL_IMAGE__": env_param(
                    "kubectl_image",
                    "bitnami/kubectl@sha256:f6dd048d1c14d89ede9636cd6bee0ff0238579c33ea1e51b2fb1a1cfd62ea246",
                ),
            },
            namespace=namespace,
        )
        try:
            wait_job(namespace, f"{cluster_prefix}-job-runner", timeout="600s")
            break
        except RuntimeError:
            if attempt >= 3:
                raise
            run(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "delete",
                    "job",
                    f"{cluster_prefix}-job-runner",
                    "--ignore-not-found=true",
                    "--wait=true",
                ]
            )
            time.sleep(10)
    result = capture(["kubectl", "-n", namespace, "logs", f"job/{cluster_prefix}-job-runner"]).splitlines()
    lines = [line.strip() for line in result if line.strip()]
    final = env_required("BENCH_PARAM_EXPECTED_OUTPUT")
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("message"):
            final = str(payload["message"])
            break
    else:
        if lines:
            final = lines[-1]
    payload = capture(
        [
            "kubectl",
            "-n",
            namespace,
            "create",
            "configmap",
            f"{cluster_prefix}-job-result",
            f"--from-literal=result={final}",
            "--dry-run=client",
            "-o",
            "yaml",
        ]
    )
    run(["kubectl", "-n", namespace, "apply", "--validate=false", "-f", "-"], input_text=payload)


def solve_ray_worker_scale() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    cluster_prefix = env_required("BENCH_PARAM_CLUSTER_PREFIX")
    target = env_required("BENCH_PARAM_TARGET_WORKER_REPLICAS")
    run(["kubectl", "-n", namespace, "scale", f"deployment/{cluster_prefix}-worker", f"--replicas={target}"])
    rollout_status(namespace, f"deployment/{cluster_prefix}-worker", timeout="180s")


def solve_ray_version_upgrade() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    cluster_prefix = env_required("BENCH_PARAM_CLUSTER_PREFIX")
    target = env_required("BENCH_PARAM_TO_IMAGE")
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "set",
            "image",
            f"deployment/{cluster_prefix}-head",
            f"ray-head={target}",
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "set",
            "image",
            f"deployment/{cluster_prefix}-worker",
            f"ray-worker={target}",
        ]
    )
    rollout_status(namespace, f"deployment/{cluster_prefix}-head", timeout="180s")
    rollout_status(namespace, f"deployment/{cluster_prefix}-worker", timeout="180s")


def solve_ray_cluster_teardown() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    cluster_prefix = env_required("BENCH_PARAM_CLUSTER_PREFIX")
    run(["kubectl", "-n", namespace, "delete", "deployment", f"{cluster_prefix}-head", "--ignore-not-found=true"])
    run(["kubectl", "-n", namespace, "delete", "deployment", f"{cluster_prefix}-worker", "--ignore-not-found=true"])
    run(["kubectl", "-n", namespace, "delete", "service", f"{cluster_prefix}-head", "--ignore-not-found=true"])


def solve_nginx_route() -> None:
    app_ns = namespace_alias("APP")
    ingress_ns = namespace_alias("INGRESS")
    service_name = env_required("BENCH_PARAM_SERVICE_NAME")
    ingress_name = env_required("BENCH_PARAM_INGRESS_NAME")
    ingress_class = env_required("BENCH_PARAM_INGRESS_CLASS_NAME")
    host = env_required("BENCH_PARAM_HOST")
    path = env_required("BENCH_PARAM_PATH")
    service_port = env_required("BENCH_PARAM_SERVICE_PORT")
    target_port = env_required("BENCH_PARAM_TARGET_PORT")
    app_name = env_param("app_deployment_name", "demo-app")
    curl_pod = env_param("curl_pod_name", "curl-test")
    payload = f"""apiVersion: v1
kind: Service
metadata:
  name: {service_name}
  namespace: {app_ns}
spec:
  selector:
    app: {app_name}
  ports:
  - port: {service_port}
    targetPort: {target_port}
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {ingress_name}
  namespace: {app_ns}
spec:
  ingressClassName: {ingress_class}
  rules:
  - host: {host}
    http:
      paths:
      - path: {path}
        pathType: Prefix
        backend:
          service:
            name: {service_name}
            port:
              number: {service_port}
    """
    run(["kubectl", "apply", "--validate=false", "-f", "-"], input_text=payload)
    expected_body = env_required("BENCH_PARAM_EXPECTED_BODY")
    controller_ip = service_cluster_ip(ingress_ns, "ingress-nginx-controller")
    curl_cmd = [
        "kubectl",
        "-n",
        app_ns,
        "exec",
        curl_pod,
        "--",
        "sh",
        "-c",
        (
            "body=$(curl -sS -H "
            f"'Host: {host}' http://{controller_ip}{path} || true); "
            f"[ \"$body\" = \"{expected_body}\" ]"
        ),
    ]
    wait_until(
        lambda: run_ok(curl_cmd),
        timeout_sec=90,
        interval_sec=2,
        err="ingress route did not return the expected body",
    )


def solve_nginx_https() -> None:
    app_ns = namespace_alias("APP")
    ingress_ns = namespace_alias("INGRESS")
    host = env_required("BENCH_PARAM_HOST")
    path = env_required("BENCH_PARAM_PATH")
    secret_name = env_required("BENCH_PARAM_TLS_SECRET_NAME")
    ingress_name = env_required("BENCH_PARAM_INGRESS_NAME")
    service_name = env_required("BENCH_PARAM_SERVICE_NAME")
    ingress_class = env_required("BENCH_PARAM_INGRESS_CLASS_NAME")
    expected_body = env_required("BENCH_PARAM_EXPECTED_BODY")
    curl_pod = env_param("curl_pod_name", "curl-test")
    service_port = (
        capture_maybe(
            [
                "kubectl",
                "-n",
                app_ns,
                "get",
                "service",
                service_name,
                "-o",
                "jsonpath={.spec.ports[0].port}",
            ]
        )
        or "5678"
    ).strip()
    with tempfile.TemporaryDirectory() as td:
        cert = Path(td) / "tls.crt"
        key = Path(td) / "tls.key"
        run(
            [
                "openssl",
                "req",
                "-x509",
                "-nodes",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "30",
                "-subj",
                f"/CN={host}",
                "-addext",
                f"subjectAltName = DNS:{host}",
            ]
        )
        secret_yaml = capture(
            [
                "kubectl",
                "-n",
                app_ns,
                "create",
                "secret",
                "tls",
                secret_name,
                f"--cert={cert}",
                f"--key={key}",
                "--dry-run=client",
                "-o",
                "yaml",
            ]
        )
        run(["kubectl", "-n", app_ns, "apply", "--validate=false", "-f", "-"], input_text=secret_yaml)
    ingress_yaml = f"""apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {ingress_name}
  namespace: {app_ns}
spec:
  ingressClassName: {ingress_class}
  tls:
  - hosts:
    - {host}
    secretName: {secret_name}
  rules:
  - host: {host}
    http:
      paths:
      - path: {path}
        pathType: Prefix
        backend:
              service:
                name: {service_name}
                port:
                  number: {service_port}
    """
    run(["kubectl", "-n", app_ns, "apply", "--validate=false", "-f", "-"], input_text=ingress_yaml)
    controller_ip = service_cluster_ip(ingress_ns, "ingress-nginx-controller")
    curl_cmd = [
        "kubectl",
        "-n",
        app_ns,
        "exec",
        curl_pod,
        "--",
        "sh",
        "-c",
        (
            "body=$(curl -k -sS --connect-to "
            f"{host}:443:{controller_ip}:443 "
            f"https://{host}{path} || true); "
            f"[ \"$body\" = \"{expected_body}\" ]"
        ),
    ]
    wait_until(
        lambda: run_ok(curl_cmd),
        timeout_sec=90,
        interval_sec=2,
        err="https ingress did not return the expected body",
    )


def solve_nginx_class_routing() -> None:
    app_ns = namespace_alias("APP")
    ingress_ns = namespace_alias("INGRESS")
    ingress_name = env_required("BENCH_PARAM_INGRESS_NAME")
    host = env_required("BENCH_PARAM_HOST")
    path = env_required("BENCH_PARAM_PATH")
    curl_pod = env_param("curl_pod_name", "curl-test")
    ingress_class = env_required("BENCH_PARAM_INGRESS_CLASS_NAME")
    expected_body = env_required("BENCH_PARAM_EXPECTED_BODY")
    run(
        [
            "kubectl",
            "-n",
            app_ns,
            "patch",
            "ingress",
            ingress_name,
            "--type",
            "merge",
            "-p",
            json.dumps({"spec": {"ingressClassName": ingress_class}}),
        ]
    )
    controller_ip = service_cluster_ip(ingress_ns, "ingress-nginx-controller")
    curl_cmd = [
        "kubectl",
        "-n",
        app_ns,
        "exec",
        curl_pod,
        "--",
        "sh",
        "-c",
        (
            "body=$(curl -sS -H "
            f"'Host: {host}' http://{controller_ip}{path} || true); "
            f"[ \"$body\" = \"{expected_body}\" ]"
        ),
    ]
    wait_until(
        lambda: run_ok(curl_cmd),
        timeout_sec=90,
        interval_sec=2,
        err="ingress class routing did not return the expected body",
    )


def solve_nginx_canary() -> None:
    app_ns = namespace_alias("APP")
    ingress_ns = namespace_alias("INGRESS")
    canary_ingress = env_required("BENCH_PARAM_CANARY_INGRESS_NAME")
    header_name = env_required("BENCH_PARAM_HEADER_NAME")
    header_value = env_required("BENCH_PARAM_HEADER_VALUE")
    host = env_required("BENCH_PARAM_HOST")
    path = env_required("BENCH_PARAM_PATH")
    curl_pod = env_param("curl_pod_name", "curl-test")
    stable_body = env_required("BENCH_PARAM_STABLE_BODY")
    canary_body = env_required("BENCH_PARAM_CANARY_BODY")
    run(
        [
            "kubectl",
            "-n",
            app_ns,
            "annotate",
            "ingress",
            canary_ingress,
            f"nginx.ingress.kubernetes.io/canary-by-header-value={header_value}",
            "--overwrite",
        ]
    )
    controller_ip = service_cluster_ip(ingress_ns, "ingress-nginx-controller")
    stable_cmd = [
        "kubectl",
        "-n",
        app_ns,
        "exec",
        curl_pod,
        "--",
        "sh",
        "-c",
        (
            "body=$(curl -sS -H "
            f"'Host: {host}' http://{controller_ip}{path} || true); "
            f"[ \"$body\" = \"{stable_body}\" ]"
        ),
    ]
    canary_cmd = [
        "kubectl",
        "-n",
        app_ns,
        "exec",
        curl_pod,
        "--",
        "sh",
        "-c",
        (
            "body=$(curl -sS -H "
            f"'Host: {host}' -H '{header_name}: {header_value}' "
            f"http://{controller_ip}{path} || true); "
            f"[ \"$body\" = \"{canary_body}\" ]"
        ),
    ]
    wait_until(
        lambda: run_ok(stable_cmd) and run_ok(canary_cmd),
        timeout_sec=90,
        interval_sec=2,
        err="canary routing did not converge",
    )


def solve_nginx_rate_limit() -> None:
    app_ns = namespace_alias("APP")
    ingress_ns = namespace_alias("INGRESS")
    host = env_required("BENCH_PARAM_HOST")
    api_path = env_required("BENCH_PARAM_API_PATH")
    curl_pod = env_param("curl_pod_name", "curl-test")
    api_ingress_name = env_required("BENCH_PARAM_API_INGRESS_NAME")
    limit_code = env_required("BENCH_PARAM_LIMIT_STATUS_CODE")
    limit_rps = env_required("BENCH_PARAM_LIMIT_RPS")
    limit_burst = env_required("BENCH_PARAM_LIMIT_BURST")
    request_count = env_required("BENCH_PARAM_REQUEST_COUNT")
    pause = env_required("BENCH_PARAM_REQUEST_PAUSE_SECONDS")
    min_limited = env_required("BENCH_PARAM_MIN_LIMITED_RESPONSES")
    run(
        [
            "kubectl",
            "-n",
            ingress_ns,
            "patch",
            "configmap",
            "ingress-nginx-controller",
            "--type=merge",
            "-p",
            json.dumps({"data": {"limit-req-status-code": limit_code}}),
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            ingress_ns,
            "patch",
            "service",
            "ingress-nginx-controller",
            "--type=merge",
            "-p",
            json.dumps({"spec": {"sessionAffinity": "ClientIP"}}),
        ]
    )
    run(["kubectl", "-n", ingress_ns, "rollout", "restart", "deployment/ingress-nginx-controller"])
    rollout_status(ingress_ns, "deployment/ingress-nginx-controller", timeout="180s")
    annotate_cmd = [
        "kubectl",
        "-n",
        app_ns,
        "annotate",
        "ingress",
        api_ingress_name,
        f"nginx.ingress.kubernetes.io/limit-rps={limit_rps}",
        f"nginx.ingress.kubernetes.io/limit-burst={limit_burst}",
        "--overwrite",
    ]
    wait_until(
        lambda: run_ok(annotate_cmd),
        timeout_sec=45,
        interval_sec=3,
        err="rate-limit ingress annotation did not succeed",
    )
    oracle_cmd = [
        "python3",
        "resources/nginx-ingress/rate_limit_ingress/oracle/oracle.py",
        "--curl-pod-name",
        curl_pod,
        "--host",
        host,
        "--api-path",
        api_path,
        "--expected-limit-status-code",
        limit_code,
        "--request-count",
        request_count,
        "--request-pause-seconds",
        pause,
        "--min-limited-responses",
        min_limited,
    ]
    wait_until(
        lambda: run_ok(oracle_cmd),
        timeout_sec=120,
        interval_sec=3,
        err="rate-limit oracle did not pass",
    )


def solve_nginx_otel() -> None:
    app_ns = namespace_alias("APP")
    ingress_ns = namespace_alias("INGRESS")
    ingress_name = env_required("BENCH_PARAM_INGRESS_NAME")
    collector_ns = namespace_alias("OTEL")
    collector_name = env_required("BENCH_PARAM_COLLECTOR_SERVICE_NAME")
    collector_port = env_required("BENCH_PARAM_COLLECTOR_PORT")
    host = env_required("BENCH_PARAM_HOST")
    path = env_required("BENCH_PARAM_PATH")
    curl_pod = env_param("curl_pod_name", "curl-test")
    otel_log_format = env_required("BENCH_PARAM_OTEL_LOG_FORMAT")
    run(
        [
            "kubectl",
            "-n",
            ingress_ns,
            "patch",
            "configmap",
            "ingress-nginx-controller",
            "--type=merge",
            "-p",
            json.dumps(
                {
                    "data": {
                        "enable-opentelemetry": "true",
                        "otlp-collector-host": f"{collector_name}.{collector_ns}.svc.cluster.local",
                        "otlp-collector-port": collector_port,
                        "otel-sampler": "AlwaysOn",
                        "otel-sampler-ratio": "1.0",
                        "log-format-upstream": otel_log_format,
                    }
                }
            ),
        ]
    )
    run(
        [
            "kubectl",
            "-n",
            app_ns,
            "annotate",
            "ingress",
            ingress_name,
            "nginx.ingress.kubernetes.io/enable-opentelemetry=true",
            "--overwrite",
        ]
    )
    run(["kubectl", "-n", ingress_ns, "rollout", "restart", "deployment/ingress-nginx-controller"])
    rollout_status(ingress_ns, "deployment/ingress-nginx-controller", timeout="180s")
    base = [
        "python3",
        "resources/nginx-ingress/otel_ingress_logging_ready/oracle/oracle.py",
        "--check",
        "--ingress-name",
        ingress_name,
        "--curl-pod-name",
        curl_pod,
        "--host",
        host,
        "--path",
        path,
        "--collector-service-name",
        collector_name,
        "--collector-port",
        collector_port,
    ]
    wait_until(
        lambda: run_ok(base[:3] + ["annotation"] + base[3:])
        and run_ok(base[:3] + ["configmap"] + base[3:])
        and run_ok(base[:3] + ["telemetry"] + base[3:]),
        timeout_sec=120,
        interval_sec=3,
        err="otel ingress oracle did not pass",
    )


def solve_rabbitmq_manual_monitoring() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    cluster_prefix = env_required("BENCH_PARAM_CLUSTER_PREFIX")
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "apply",
            "-f",
            "resources/rabbitmq-experiments/manual_monitoring/resource/prometheus-deployment.yaml",
        ]
    )
    prometheus_config = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-config
data:
  prometheus.yml: |
    global:
      scrape_interval: 15s
    scrape_configs:
      - job_name: "prometheus"
        static_configs:
          - targets: ["localhost:9090"]
      - job_name: "rabbitmq"
        static_configs:
          - targets:
            - "{cluster_prefix}-0.{cluster_prefix}-headless.{namespace}.svc.cluster.local:15692"
            - "{cluster_prefix}-1.{cluster_prefix}-headless.{namespace}.svc.cluster.local:15692"
            - "{cluster_prefix}-2.{cluster_prefix}-headless.{namespace}.svc.cluster.local:15692"
"""
    run(["kubectl", "-n", namespace, "apply", "--validate=false", "-f", "-"], input_text=prometheus_config)
    run(["kubectl", "-n", namespace, "rollout", "restart", "deploy/prometheus"])
    rollout_status(namespace, "deploy/prometheus", timeout="300s")
    wait_until(
        lambda: run_ok(
            [
                "python3",
                "resources/rabbitmq-experiments/manual_monitoring/setup_precondition_check.py",
                "--namespace",
                namespace,
                "--targets-ready-only",
            ]
        ),
        timeout_sec=180,
        interval_sec=5,
        err="prometheus targets did not become ready",
    )


def solve_rabbitmq_manual_runtime_reset() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    prefix = env_required("BENCH_PARAM_CLUSTER_PREFIX")
    stale_vhost = env_required("BENCH_PARAM_STALE_VHOST")
    stale_user = env_required("BENCH_PARAM_STALE_USER")
    stale_policy = env_required("BENCH_PARAM_STALE_POLICY")
    canonical_queue = env_required("BENCH_PARAM_CANONICAL_QUEUE")
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "exec",
            f"{prefix}-0",
            "--",
            "rabbitmqctl",
            "clear_policy",
            "-p",
            "/app",
            stale_policy,
        ]
    )
    run(["kubectl", "-n", namespace, "exec", f"{prefix}-0", "--", "rabbitmqctl", "delete_user", stale_user])
    run(["kubectl", "-n", namespace, "exec", f"{prefix}-0", "--", "rabbitmqctl", "delete_vhost", stale_vhost])
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "exec",
            f"{prefix}-0",
            "--",
            "rabbitmqctl",
            "purge_queue",
            "-p",
            "/app",
            canonical_queue,
        ]
    )


def _tsv_lines(output: str) -> tuple[list[str], list[list[str]]]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return [], []
    header = lines[0].split("\t")
    rows = [line.split("\t") for line in lines[1:]]
    return header, rows


def _cockroach_show_setting(namespace: str, prefix: str, setting_name: str) -> str:
    output = cockroach_sql(namespace, prefix, f"SHOW CLUSTER SETTING {setting_name};")
    values = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == setting_name:
            continue
        if set(stripped) == {"-"}:
            continue
        values.append(stripped)
    return values[-1] if values else ""


def _cockroach_wait_setting(namespace: str, prefix: str, setting_name: str, predicate, *, err: str) -> None:
    wait_until(
        lambda: predicate(_cockroach_show_setting(namespace, prefix, setting_name)),
        timeout_sec=180,
        interval_sec=5,
        err=err,
    )


def solve_cockroach_deploy() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    prefix = cockroach_prefix()
    apply_cockroach_manifest("resources/cockroachdb/deploy/resource/rbac.yaml", namespace)
    apply_cockroach_manifest("resources/cockroachdb/deploy/resource/services.yaml", namespace)
    apply_cockroach_manifest("resources/cockroachdb/deploy/resource/statefulset.yaml", namespace)
    apply_cockroach_manifest("resources/cockroachdb/deploy/resource/pdb.yaml", namespace)
    wait_cockroach_pods_running(namespace, prefix, 3, timeout_sec=900)
    cockroach_init_insecure(namespace, prefix)
    cockroach_wait_sql(namespace, prefix)
    wait_statefulset_ready(namespace, prefix, 3, timeout_sec=300)


def solve_cockroach_initialize() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    prefix = cockroach_prefix()
    apply_cockroach_manifest("resources/cockroachdb/initialize/resource/statefulset.yaml", namespace)
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "delete",
            "pod",
            cockroach_pod(prefix, 0),
            cockroach_pod(prefix, 1),
            cockroach_pod(prefix, 2),
            "--ignore-not-found=true",
        ]
    )
    wait_cockroach_pods_running(namespace, prefix, 3, timeout_sec=900)

    host = cockroach_host(prefix, namespace, 0)

    def init_ready() -> bool:
        proc = subprocess.run(
            [
                "kubectl",
                "-n",
                namespace,
                "exec",
                cockroach_pod(prefix, 0),
                "--",
                "./cockroach",
                "init",
                "--insecure",
                f"--host={host}",
            ],
            text=True,
            cwd=str(REPO_ROOT),
            env=os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode == 0:
            return True
        text = f"{proc.stdout}\n{proc.stderr}".lower()
        return "already been initialized" in text

    wait_until(
        init_ready,
        timeout_sec=120,
        interval_sec=3,
        err="cockroach init did not succeed",
    )
    cockroach_wait_sql(namespace, prefix)
    wait_statefulset_ready(namespace, prefix, 3, timeout_sec=300)


def solve_cockroach_cluster_settings() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    prefix = cockroach_prefix()
    setting_name = env_param("setting_name", "kv.snapshot_rebalance.max_rate")
    cockroach_sql(namespace, prefix, f"SET CLUSTER SETTING {setting_name} = '128MiB';")
    _cockroach_wait_setting(
        namespace,
        prefix,
        setting_name,
        lambda value: "128" in value.lower() or "134217728" in value,
        err=f"{setting_name} did not update",
    )


def solve_cockroach_version_check() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    prefix = cockroach_prefix()
    report_cm = env_param("report_configmap_name", "crdb-version-report")
    report_key = env_param("report_key", "db_version")
    version_value = _cockroach_show_setting(namespace, prefix, "version")
    create_or_apply_configmap(namespace, report_cm, {report_key: version_value})


def solve_cockroach_major_upgrade_finalize() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    prefix = cockroach_prefix()
    from_version = env_param("from_version", "23.2.0")
    to_version = env_param("to_version", "24.1.0")
    target_image = cockroach_image_ref(to_version)
    from_family = ".".join(from_version.lstrip("v").split(".")[:2])
    family = ".".join(to_version.split(".")[:2])
    set_workload_container_image(namespace, f"statefulset/{prefix}", "db", target_image)
    rollout_status(namespace, f"statefulset/{prefix}", timeout="900s")
    wait_statefulset_ready(namespace, prefix, 3, timeout_sec=900)
    cockroach_wait_sql(namespace, prefix)
    _cockroach_wait_setting(
        namespace,
        prefix,
        "cluster.preserve_downgrade_option",
        lambda value: value == from_family,
        err="preserve_downgrade_option did not stay at the source version during binary upgrade",
    )
    cockroach_sql(namespace, prefix, "RESET CLUSTER SETTING cluster.preserve_downgrade_option;")
    _cockroach_wait_setting(
        namespace,
        prefix,
        "version",
        lambda value: family in value or to_version in value,
        err="cluster version did not finalize",
    )
    _cockroach_wait_setting(
        namespace,
        prefix,
        "cluster.preserve_downgrade_option",
        lambda value: value in ("", "NULL", "[]"),
        err="preserve_downgrade_option did not clear",
    )
    wait_statefulset_ready(namespace, prefix, 3, timeout_sec=300)
    cockroach_wait_sql(namespace, prefix)


def solve_cockroach_partitioned_update() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    prefix = cockroach_prefix()
    to_version = env_param("to_version", "24.1.1")
    target_image = cockroach_image_ref(to_version)
    final_partition = int(env_param("update_partition", "0"))

    def pod_image(ordinal: int) -> str:
        return capture(
            [
                "kubectl",
                "-n",
                namespace,
                "get",
                "pod",
                cockroach_pod(prefix, ordinal),
                "-o",
                "jsonpath={.spec.containers[0].image}",
            ]
        ).strip()

    def partition_updated(partition: int) -> bool:
        try:
            current_partition = int(
                capture(
                    [
                        "kubectl",
                        "-n",
                        namespace,
                        "get",
                        "statefulset",
                        prefix,
                        "-o",
                        "jsonpath={.spec.updateStrategy.rollingUpdate.partition}",
                    ]
                ).strip()
                or "0"
            )
        except Exception:
            return False
        if current_partition != partition:
            return False
        try:
            if statefulset_ready_replicas(namespace, prefix) != 3:
                return False
        except Exception:
            return False
        for ordinal in range(3):
            expected = target_image if ordinal >= partition else cockroach_image_ref(env_param("from_version", "24.1.0"))
            try:
                if pod_image(ordinal) != expected:
                    return False
            except Exception:
                return False
        return True

    set_workload_container_image(namespace, f"statefulset/{prefix}", "db", target_image)
    for partition in range(2, final_partition - 1, -1):
        replace_json(
            namespace,
            f"statefulset/{prefix}",
            {"spec": {"updateStrategy": {"rollingUpdate": {"partition": partition}}}},
        )
        wait_until(
            lambda p=partition: partition_updated(p),
            timeout_sec=900,
            interval_sec=5,
            err=f"cockroach partitioned rollout did not settle at partition {partition}",
        )
        cockroach_wait_sql(namespace, prefix)
    wait_statefulset_ready(namespace, prefix, 3, timeout_sec=900)
    cockroach_wait_sql(namespace, prefix)


def solve_cockroach_decommission() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    prefix = cockroach_prefix()
    to_replicas = int(env_param("to_replica_count", "3"))
    output = cockroach_capture(
        namespace,
        prefix,
        ["./cockroach", "node", "status", "--insecure", "--format=tsv"],
    )
    header, rows = _tsv_lines(output)
    columns = {name: idx for idx, name in enumerate(header)}
    node_id = None
    address_idx = columns.get("address")
    id_idx = columns.get("id")
    if address_idx is None or id_idx is None:
        raise RuntimeError("could not parse cockroach node status")
    target_pod = f"{prefix}-{to_replicas}"
    for row in rows:
        if len(row) <= max(address_idx, id_idx):
            continue
        if target_pod in row[address_idx]:
            node_id = row[id_idx]
            break
    if not node_id:
        raise RuntimeError(f"could not find node id for {target_pod}")
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "exec",
            cockroach_pod(prefix, 0),
            "--",
            "./cockroach",
            "node",
            "decommission",
            node_id,
            "--insecure",
            "--wait=all",
        ]
    )
    run(["kubectl", "-n", namespace, "scale", f"statefulset/{prefix}", f"--replicas={to_replicas}"])
    rollout_status(namespace, f"statefulset/{prefix}", timeout="900s")
    wait_statefulset_ready(namespace, prefix, to_replicas, timeout_sec=900)


def solve_cockroach_health_check_recovery() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    prefix = cockroach_prefix()
    apply_cockroach_manifest("resources/cockroachdb/health-check-recovery/resource/statefulset.yaml", namespace)
    rollout_status(namespace, f"statefulset/{prefix}", timeout="900s")
    wait_statefulset_ready(namespace, prefix, 3, timeout_sec=900)
    cockroach_wait_sql(namespace, prefix)


def solve_cockroach_generate_cert() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    prefix = cockroach_prefix()
    secret_name = env_param("cert_secret_name", "crdb-cluster-certs")
    days = env_param("cert_validity_days", "365")
    material = generate_cockroach_tls_material(namespace, prefix, days)
    apply_cockroach_tls_secret(namespace, secret_name, material)
    apply_cockroach_manifest("resources/cockroachdb/certificate-rotation/resource/statefulset.yaml", namespace)
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "delete",
            "pod",
            cockroach_pod(prefix, 0),
            cockroach_pod(prefix, 1),
            cockroach_pod(prefix, 2),
            "--wait=false",
            "--ignore-not-found=true",
        ]
    )
    wait_cockroach_statefulset_revision(namespace, prefix, 3, timeout_sec=900)
    cockroach_wait_sql(namespace, prefix, secure=True)


def solve_cockroach_certificate_rotation() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    prefix = cockroach_prefix()
    secret_name = env_param("cert_secret_name", "crdb-cluster-certs")
    min_days = int(env_param("min_rotated_validity_days", "300"))
    old_secret = decode_secret_data(namespace, secret_name)
    ca_key = capture(
        [
            "kubectl",
            "-n",
            namespace,
            "exec",
            "openssl-toolbox",
            "--",
            "cat",
            "/tmp/certs/ca.key",
        ]
    ).encode("utf-8")
    rotated = generate_cockroach_tls_material(
        namespace,
        prefix,
        str(min_days + 7),
        reuse_ca={
            "ca.crt": old_secret["ca.crt"],
            "ca.key": ca_key,
        },
    )
    rotated["client.root.crt"] = old_secret["client.root.crt"]
    rotated["client.root.key"] = old_secret["client.root.key"]
    apply_cockroach_tls_secret(namespace, secret_name, rotated)
    run(["kubectl", "-n", namespace, "rollout", "restart", f"statefulset/{prefix}"])
    rollout_status(namespace, f"statefulset/{prefix}", timeout="900s")
    wait_statefulset_ready(namespace, prefix, 3, timeout_sec=900)
    cockroach_wait_sql(namespace, prefix, secure=True)


def solve_cockroach_expose_ingress() -> None:
    namespace = namespace_alias("DEFAULT")
    ingress_ns = namespace_alias("INGRESS")
    prefix = cockroach_prefix()
    ui_host = env_param("ui_host", "crdb-ui.example.com")
    ingress_class = env_param("ingress_class_name", "nginx")
    tls_secret = env_param("tls_secret_name", "crdb-ui-tls")
    sql_port = env_param("sql_port", "26257")
    create_or_apply_configmap(
        ingress_ns,
        "tcp-services",
        {sql_port: f"{namespace}/{prefix}:{sql_port}"},
    )
    controller = json.loads(
        capture(
            [
                "kubectl",
                "-n",
                ingress_ns,
                "get",
                "deployment",
                "ingress-nginx-controller",
                "-o",
                "json",
            ]
        )
    )
    args = controller["spec"]["template"]["spec"]["containers"][0].get("args") or []
    wanted = f"--tcp-services-configmap={ingress_ns}/tcp-services"
    controller_changed = False
    if wanted not in args:
        patch_json(
            ingress_ns,
            "deployment/ingress-nginx-controller",
            [{"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": wanted}],
        )
        controller_changed = True
    replace_json(
        ingress_ns,
        "service/ingress-nginx-controller",
        {
            "spec": {
                "ports": [
                    {"name": "http", "port": 80, "protocol": "TCP", "targetPort": "http"},
                    {"name": "https", "port": 443, "protocol": "TCP", "targetPort": "https"},
                    {"name": "cockroach-sql", "port": int(sql_port), "protocol": "TCP", "targetPort": int(sql_port)},
                ]
            }
        },
    )
    if controller_changed:
        rollout_status(ingress_ns, "deployment/ingress-nginx-controller", timeout="300s")

    def admission_ready() -> bool:
        try:
            secret_name = capture(
                [
                    "kubectl",
                    "-n",
                    ingress_ns,
                    "get",
                    "secret",
                    "ingress-nginx-admission",
                    "-o",
                    "jsonpath={.metadata.name}",
                ]
            ).strip()
            if secret_name != "ingress-nginx-admission":
                return False
            webhook_namespace = capture(
                [
                    "kubectl",
                    "get",
                    "validatingwebhookconfiguration",
                    "ingress-nginx-admission",
                    "-o",
                    "jsonpath={.webhooks[0].clientConfig.service.namespace}",
                ]
            ).strip()
            if webhook_namespace != ingress_ns:
                return False
            ca_bundle = capture(
                [
                    "kubectl",
                    "get",
                    "validatingwebhookconfiguration",
                    "ingress-nginx-admission",
                    "-o",
                    "jsonpath={.webhooks[0].clientConfig.caBundle}",
                ]
            ).strip()
            if not ca_bundle:
                return False
            endpoint_ip = capture(
                [
                    "kubectl",
                    "-n",
                    ingress_ns,
                    "get",
                    "endpoints",
                    "ingress-nginx-controller-admission",
                    "-o",
                    "jsonpath={.subsets[0].addresses[0].ip}",
                ]
            ).strip()
        except BaseException:
            return False
        return bool(endpoint_ip)

    wait_until(
        admission_ready,
        timeout_sec=180,
        interval_sec=3,
        err="ingress-nginx admission webhook did not become ready",
    )
    ingress_yaml = f"""apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {prefix}-ui
  namespace: {namespace}
spec:
  ingressClassName: {ingress_class}
  tls:
  - hosts:
    - {ui_host}
    secretName: {tls_secret}
  rules:
  - host: {ui_host}
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: {prefix}-public
            port:
              number: 8080
"""
    run(["kubectl", "-n", namespace, "apply", "--validate=false", "-f", "-"], input_text=ingress_yaml)
    rollout_status(ingress_ns, "deployment/ingress-nginx-controller", timeout="300s")

    def ui_route_ready() -> bool:
        try:
            status = capture(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "exec",
                    "curl-test",
                    "--",
                    "curl",
                    "-sS",
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
                    "-H",
                    f"Host: {ui_host}",
                    f"http://ingress-nginx-controller.{ingress_ns}.svc/",
                ]
            ).strip()
        except BaseException:
            return False
        return status.isdigit() and 200 <= int(status) < 400

    wait_until(
        ui_route_ready,
        timeout_sec=180,
        interval_sec=3,
        err="cockroach UI ingress route did not become reachable",
    )


def solve_cockroach_monitoring() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    service_monitor = env_param("service_monitor_name", "crdb-servicemonitor")
    metrics_path = env_param("metrics_path", "/_status/vars")
    metrics_port = env_param("metrics_port", "8080")
    monitor_yaml = f"""apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
metadata:
  name: {service_monitor}
  namespace: {namespace}
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: cockroachdb
  podMetricsEndpoints:
  - port: http
    path: {metrics_path}
    interval: 30s
"""
    run(["kubectl", "-n", namespace, "apply", "--validate=false", "-f", "-"], input_text=monitor_yaml)
    wait_until(
        lambda: run_ok(["python3", "resources/cockroachdb/monitoring-integration/oracle/oracle.py"]),
        timeout_sec=300,
        interval_sec=5,
        err="cockroach monitoring did not converge",
    )


def solve_cockroach_zone_config() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    prefix = cockroach_prefix()
    target_schema = env_param("target_schema", "tenant_b")
    num_replicas = env_param("num_replicas", "3")
    gc_ttl = env_param("gc_ttl_seconds", "14400")
    range_min = env_param("range_min_bytes", "134217728")
    range_max = env_param("range_max_bytes", "536870912")
    list_sql = (
        "SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema = '{target_schema}' AND table_type = 'BASE TABLE' ORDER BY table_name;"
    )
    header, rows = _tsv_lines(cockroach_sql(namespace, prefix, list_sql))
    table_names = [row[0] for row in rows if row]
    if not table_names and header and header[0] != "table_name":
        table_names = [header[0]]
    for table in table_names:
        sql = (
            f"ALTER TABLE {target_schema}.{table} CONFIGURE ZONE USING "
            f"num_replicas = {num_replicas}, gc.ttlseconds = {gc_ttl}, "
            f"range_min_bytes = {range_min}, range_max_bytes = {range_max};"
        )
        cockroach_sql(namespace, prefix, sql)


def mongodb_namespace() -> str:
    return env_required("BENCH_NAMESPACE")


def mongodb_prefix(param_name: str = "cluster_prefix", default: str = "mongodb-replica") -> str:
    return env_param(param_name, default)


def mongodb_service(param_name: str = "headless_service_name", default: str = "mongodb-replica-svc") -> str:
    return env_param(param_name, default)


def mongodb_replicas(param_name: str = "expected_replicas", default: str = "3") -> int:
    return int(env_param(param_name, default))


def mongodb_pod(prefix: str, ordinal: int = 0) -> str:
    return f"{prefix}-{ordinal}"


def mongodb_host(prefix: str, service: str, namespace: str, ordinal: int) -> str:
    return f"{prefix}-{ordinal}.{service}.{namespace}.svc.cluster.local:27017"


def mongodb_hosts(prefix: str, service: str, namespace: str, replicas: int) -> list[str]:
    return [mongodb_host(prefix, service, namespace, ordinal) for ordinal in range(replicas)]


def mongodb_members_js(
    prefix: str,
    service: str,
    namespace: str,
    replicas: int,
    *,
    arbiter_host: str | None = None,
    horizons: list[str] | None = None,
) -> str:
    members: list[dict[str, object]] = []
    for ordinal in range(replicas):
        member: dict[str, object] = {
            "_id": ordinal,
            "host": mongodb_host(prefix, service, namespace, ordinal),
        }
        if horizons:
            member["horizons"] = {"horizon1": horizons[ordinal]}
        members.append(member)
    if arbiter_host:
        members.append(
            {
                "_id": replicas,
                "host": arbiter_host,
                "arbiterOnly": True,
            }
        )
    return json.dumps(members, separators=(",", ":"))


def mongodb_secret_value(namespace: str, name: str, key: str = "password") -> str:
    raw = capture(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "secret",
            name,
            "-o",
            f"jsonpath={{.data.{key}}}",
        ]
    ).strip()
    if not raw:
        raise RuntimeError(f"secret/{name} missing key {key}")
    return base64.b64decode(raw).decode("utf-8")


def mongodb_uri(namespace: str, user: str, secret_name: str, db: str, *, auth_source: str = "admin") -> str:
    password = mongodb_secret_value(namespace, secret_name)
    return f"mongodb://{user}:{password}@localhost:27017/{db}?authSource={auth_source}"


def mongodb_admin_uri(namespace: str) -> str:
    return mongodb_uri(
        namespace,
        env_param("admin_username", "admin-user"),
        env_param("admin_secret_name", "admin-user-password"),
        "admin",
    )


def mongodb_replica_set_admin_uri(namespace: str, prefix: str, service: str, replicas: int) -> str:
    user = env_param("admin_username", "admin-user")
    secret_name = env_param("admin_secret_name", "admin-user-password")
    password = mongodb_secret_value(namespace, secret_name)
    replica_set = env_param("replica_set_name", prefix)
    hosts = ",".join(mongodb_host(prefix, service, namespace, ordinal) for ordinal in range(replicas))
    return f"mongodb://{user}:{password}@{hosts}/admin?replicaSet={replica_set}&authSource=admin"


def mongodb_exec(namespace: str, prefix: str, args: list[str], *, ordinal: int = 0) -> subprocess.CompletedProcess[str]:
    return _proc(["kubectl", "-n", namespace, "exec", mongodb_pod(prefix, ordinal), "--"] + args)


def mongodb_run(namespace: str, prefix: str, script: str, *, ordinal: int = 0, uri: str | None = None) -> None:
    cmd = ["kubectl", "-n", namespace, "exec", mongodb_pod(prefix, ordinal), "--", "mongosh", "--quiet"]
    if uri:
        cmd.append(uri)
    cmd.extend(["--eval", script])
    run(cmd)


def mongodb_capture(namespace: str, prefix: str, script: str, *, ordinal: int = 0, uri: str | None = None) -> str:
    cmd = ["kubectl", "-n", namespace, "exec", mongodb_pod(prefix, ordinal), "--", "mongosh", "--quiet"]
    if uri:
        cmd.append(uri)
    cmd.extend(["--eval", script])
    return capture(cmd)


def mongodb_wait_primary(namespace: str, prefix: str, *, uri: str | None = None, timeout_sec: int = 240) -> None:
    def primary_ready() -> bool:
        cmd = ["kubectl", "-n", namespace, "exec", mongodb_pod(prefix, 0), "--", "mongosh", "--quiet"]
        if uri:
            cmd.append(uri)
        cmd.extend(["--eval", "db.hello().isWritablePrimary"])
        try:
            return capture(cmd).strip() == "true"
        except BaseException:
            return False

    wait_until(
        primary_ready,
        timeout_sec=timeout_sec,
        interval_sec=5,
        err="mongodb primary did not become writable",
    )


def mongodb_wait_ready(namespace: str, prefix: str, replicas: int, *, timeout_sec: int = 600) -> None:
    wait_statefulset_ready(namespace, prefix, replicas, timeout_sec=timeout_sec)
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "wait",
            "--for=condition=ready",
            "pod",
            "-l",
            f"app={prefix}",
            "--timeout=300s",
        ]
    )


def mongodb_scale_sts(namespace: str, prefix: str, replicas: int) -> None:
    run(["kubectl", "-n", namespace, "scale", f"statefulset/{prefix}", f"--replicas={replicas}"])
    wait_statefulset_ready(namespace, prefix, replicas, timeout_sec=900)


def mongodb_restart_sts(namespace: str, prefix: str) -> None:
    run(["kubectl", "-n", namespace, "rollout", "restart", f"statefulset/{prefix}"])
    rollout_status(namespace, f"statefulset/{prefix}", timeout="900s")


def mongodb_configmap(namespace: str, name: str, data: dict[str, str]) -> None:
    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": namespace},
        "data": data,
    }
    run(["kubectl", "-n", namespace, "apply", "--validate=false", "-f", "-"], input_text=json.dumps(manifest))


def mongodb_reset_member_hosts(namespace: str, prefix: str, service: str, replicas: int, *, uri: str | None = None) -> None:
    members = mongodb_members_js(prefix, service, namespace, replicas)
    mongodb_run(
        namespace,
        prefix,
        f"cfg=rs.conf(); cfg.members={members}; cfg.version=(cfg.version||1)+1; rs.reconfig(cfg,{{force:true}});",
        uri=uri,
    )


def mongodb_create_or_update_user(
    namespace: str,
    prefix: str,
    user: str,
    password: str,
    roles: list[dict[str, str]],
    *,
    db: str = "admin",
) -> None:
    admin_uri = mongodb_admin_uri(namespace)
    script = (
        f"try {{ db.getSiblingDB({json.dumps(db)}).createUser({json.dumps({'user': user, 'pwd': password, 'roles': roles})}); }} "
        f"catch (e) {{ db.getSiblingDB({json.dumps(db)}).updateUser({json.dumps(user)}, {json.dumps({'pwd': password, 'roles': roles})}); }}"
    )
    mongodb_run(namespace, prefix, script, uri=admin_uri)


def mongodb_create_or_update_role(
    namespace: str,
    prefix: str,
    role_name: str,
    privileges: list[dict[str, object]],
    roles: list[dict[str, str]] | None = None,
    *,
    db: str,
) -> None:
    admin_uri = mongodb_admin_uri(namespace)
    payload = {
        "privileges": privileges,
        "roles": roles or [],
    }
    script = (
        f"try {{ db.getSiblingDB({json.dumps(db)}).createRole(Object.assign({json.dumps({'role': role_name})}, {json.dumps(payload)})); }} "
        f"catch (e) {{ db.getSiblingDB({json.dumps(db)}).updateRole({json.dumps(role_name)}, {json.dumps(payload)}); }}"
    )
    mongodb_run(namespace, prefix, script, uri=admin_uri)


def mongodb_drop_role(namespace: str, prefix: str, role_name: str, *, db: str) -> None:
    admin_uri = mongodb_admin_uri(namespace)
    mongodb_run(
        namespace,
        prefix,
        f"try {{ db.getSiblingDB({json.dumps(db)}).dropRole({json.dumps(role_name)}); }} catch (e) {{}}",
        uri=admin_uri,
    )


def mongodb_seed_collection(
    namespace: str,
    prefix: str,
    *,
    db: str,
    collection: str,
    docs: list[dict],
) -> None:
    admin_uri = mongodb_admin_uri(namespace)
    script = (
        f"db.getSiblingDB({json.dumps(db)}).getCollection({json.dumps(collection)}).deleteMany({{}});"
        f"db.getSiblingDB({json.dumps(db)}).getCollection({json.dumps(collection)}).insertMany({json.dumps(docs)}, {{writeConcern:{{w:1}}}});"
    )
    mongodb_run(namespace, prefix, script, uri=admin_uri)


def mongodb_direct_connection_uri(namespace: str, prefix: str, service: str) -> str:
    return (
        f"mongodb://{prefix}-0.{service}.{namespace}.svc.cluster.local:27017/"
        "?directConnection=true&serverSelectionTimeoutMS=4000&connectTimeoutMS=4000"
    )


def mongodb_tls_dns_names(namespace: str, prefix: str, service: str, replicas: int) -> list[str]:
    names = [
        "localhost",
        service,
        f"{service}.{namespace}",
        f"{service}.{namespace}.svc",
        f"{service}.{namespace}.svc.cluster.local",
    ]
    names.extend(
        f"{prefix}-{ordinal}.{service}.{namespace}.svc.cluster.local"
        for ordinal in range(replicas)
    )
    return names


def mongodb_write_server_certificate_bundle(
    output_dir: Path,
    *,
    hosts: list[str],
    common_name: str,
    valid_days: int,
    ca_cert: bytes | None = None,
    ca_key: bytes | None = None,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    san_lines = "\n".join(f"DNS.{index + 1}={host}" for index, host in enumerate(hosts))
    openssl_cnf = "\n".join(
        [
            "distinguished_name=req_distinguished_name",
            "req_extensions=v3_req",
            "prompt=no",
            "[req_distinguished_name]",
            f"CN={common_name}",
            "[v3_req]",
            "keyUsage=critical,digitalSignature,keyEncipherment",
            "extendedKeyUsage=serverAuth,clientAuth",
            "subjectAltName=@alt_names",
            "[alt_names]",
            san_lines,
        ]
    )
    (output_dir / "openssl.cnf").write_text(openssl_cnf, encoding="utf-8")

    ca_key_path = output_dir / "ca.key"
    ca_crt_path = output_dir / "ca.crt"
    if ca_cert is not None and ca_key is not None:
        ca_key_path.write_bytes(ca_key)
        ca_crt_path.write_bytes(ca_cert)
    else:
        run(["openssl", "genrsa", "-out", str(ca_key_path), "2048"])
        run(
            [
                "openssl",
                "req",
                "-x509",
                "-new",
                "-nodes",
                "-key",
                str(ca_key_path),
                "-sha256",
                "-days",
                "3650",
                "-subj",
                f"/CN={common_name}-ca",
                "-out",
                str(ca_crt_path),
            ]
        )

    server_key_path = output_dir / "server.key"
    server_csr_path = output_dir / "server.csr"
    server_crt_path = output_dir / "server.crt"
    server_pem_path = output_dir / "server.pem"
    run(["openssl", "genrsa", "-out", str(server_key_path), "2048"])
    run(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            str(server_key_path),
            "-out",
            str(server_csr_path),
            "-config",
            str(output_dir / "openssl.cnf"),
        ]
    )
    run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(server_csr_path),
            "-CA",
            str(ca_crt_path),
            "-CAkey",
            str(ca_key_path),
            "-CAcreateserial",
            "-out",
            str(server_crt_path),
            "-days",
            str(valid_days),
            "-extensions",
            "v3_req",
            "-extfile",
            str(output_dir / "openssl.cnf"),
        ]
    )
    server_pem_path.write_bytes(server_crt_path.read_bytes() + server_key_path.read_bytes())
    return {
        "ca.crt": ca_crt_path,
        "ca.key": ca_key_path,
        "server.crt": server_crt_path,
        "server.key": server_key_path,
        "server.pem": server_pem_path,
    }


def mongodb_wait_tls_primary(
    namespace: str,
    prefix: str,
    service: str,
    *,
    ordinal: int = 0,
    timeout_sec: int = 300,
) -> None:
    uri = mongodb_direct_connection_uri(namespace, prefix, service)

    def tls_cluster_ready() -> bool:
        try:
            payload = capture(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "exec",
                    mongodb_pod(prefix, ordinal),
                    "--",
                    "mongosh",
                    "--quiet",
                    uri,
                    "--tls",
                    "--tlsCAFile",
                    "/etc/mongo-ca/ca.crt",
                    "--eval",
                    (
                        "(() => {"
                        "try {"
                        "const s = rs.status();"
                        "if (!s.members || !s.members.length) return false;"
                        "const primary = s.members.filter(m => m.stateStr === \"PRIMARY\").length;"
                        "const secondary = s.members.filter(m => m.stateStr === \"SECONDARY\").length;"
                        "return primary === 1 && (primary + secondary) === s.members.length;"
                        "} catch (e) { return false; }"
                        "})()"
                    ),
                ]
            ).strip()
        except BaseException:
            return False
        return payload == "true"

    wait_until(
        tls_cluster_ready,
        timeout_sec=timeout_sec,
        interval_sec=5,
        err=f"mongodb tls replica set was not healthy for {prefix}",
    )


def solve_mongodb_deploy() -> None:
    namespace = env_required("BENCH_NAMESPACE")
    cluster_prefix = env_required("BENCH_PARAM_CLUSTER_PREFIX")
    service_name = env_required("BENCH_PARAM_HEADLESS_SERVICE_NAME")
    replica_set = env_required("BENCH_PARAM_REPLICA_SET_NAME")
    admin_user = env_required("BENCH_PARAM_ADMIN_USERNAME")
    app_user = env_required("BENCH_PARAM_APP_USERNAME")
    app_db = env_required("BENCH_PARAM_APP_DATABASE")
    admin_pw = "admin123password"
    app_pw = "app123password"

    apply_env_template("resources/mongodb/deploy/resource/secrets.yaml", namespace=namespace)
    apply_env_template("resources/mongodb/deploy/resource/services.yaml", namespace=namespace)
    apply_env_template("resources/mongodb/deploy/resource/statefulset.yaml", namespace=namespace)
    rollout_status(namespace, f"statefulset/{cluster_prefix}", timeout="600s")

    rs_cmd = (
        f'try {{ rs.status(); }} catch (e) {{ rs.initiate({{_id:"{replica_set}",members:['
        f'{{_id:0,host:"{cluster_prefix}-0.{service_name}.{namespace}.svc.cluster.local:27017"}},'
        f'{{_id:1,host:"{cluster_prefix}-1.{service_name}.{namespace}.svc.cluster.local:27017"}},'
        f'{{_id:2,host:"{cluster_prefix}-2.{service_name}.{namespace}.svc.cluster.local:27017"}}'
        "]}); }"
    )
    run(["kubectl", "-n", namespace, "exec", f"{cluster_prefix}-0", "--", "mongosh", "--quiet", "--eval", rs_cmd])
    wait_until(
        lambda: (
            capture(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "exec",
                    f"{cluster_prefix}-0",
                    "--",
                    "mongosh",
                    "--quiet",
                    "--eval",
                    "db.hello().isWritablePrimary",
                ]
            ).strip()
            == "true"
        ),
        timeout_sec=180,
        interval_sec=5,
        err="mongodb primary did not become writable",
    )

    admin_create = (
        "try { db.getSiblingDB(\"admin\").createUser({user:\""
        + admin_user
        + "\",pwd:\""
        + admin_pw
        + "\",roles:[{role:\"clusterAdmin\",db:\"admin\"},{role:\"userAdminAnyDatabase\",db:\"admin\"},{role:\"readWriteAnyDatabase\",db:\"admin\"}]}); } "
        + "catch (e) { if (!String(e).includes(\"already exists\")) throw e; }"
    )
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "exec",
            f"{cluster_prefix}-0",
            "--",
            "mongosh",
            "--quiet",
            "--eval",
            admin_create,
        ]
    )
    app_create = (
        "db.getSiblingDB(\""
        + app_db
        + "\").runCommand({createUser:\""
        + app_user
        + "\",pwd:\""
        + app_pw
        + "\",roles:[{role:\"readWrite\",db:\""
        + app_db
        + "\"}]})"
    )
    app_update = (
        "db.getSiblingDB(\""
        + app_db
        + "\").updateUser(\""
        + app_user
        + "\",{pwd:\""
        + app_pw
        + "\",roles:[{role:\"readWrite\",db:\""
        + app_db
        + "\"}]})"
    )
    run(
        [
            "kubectl",
            "-n",
            namespace,
            "exec",
            f"{cluster_prefix}-0",
            "--",
            "mongosh",
            "--quiet",
            f"mongodb://{admin_user}:{admin_pw}@localhost:27017/admin",
            "--eval",
            f"try {{ {app_create}; }} catch (e) {{ {app_update}; }}",
        ]
    )


def solve_mongodb_initialize() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    service = mongodb_service()
    replicas = mongodb_replicas()
    mongodb_reset_member_hosts(namespace, prefix, service, replicas)
    mongodb_wait_primary(namespace, prefix)


def solve_mongodb_decommission() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    service = env_param("service_name", "mongo")
    target_replicas = int(env_param("target_replicas", "2"))
    mongodb_scale_sts(namespace, prefix, target_replicas)
    mongodb_reset_member_hosts(namespace, prefix, service, target_replicas)
    mongodb_wait_primary(namespace, prefix)


def solve_mongodb_arbiters() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix("data_cluster_prefix", "mongo-rs")
    arbiter_prefix = env_param("arbiter_cluster_prefix", "mongo-arb")
    arbiter_service = env_param("arbiter_service_name", "mongo-arb")
    arbiter_host = mongodb_host(arbiter_prefix, arbiter_service, namespace, 0)
    mongodb_run(
        namespace,
        prefix,
        'db.adminCommand({setDefaultRWConcern: 1, defaultWriteConcern: {w: "majority"}, writeConcern: {w: "majority"}})',
    )
    mongodb_run(
        namespace,
        prefix,
        f'try {{ rs.addArb({json.dumps(arbiter_host)}); }} catch (e) {{ if (!String(e).includes("already")) throw e; }}',
    )
    mongodb_wait_primary(namespace, prefix)


def solve_mongodb_external_access_horizons() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    service = env_param("service_name", "mongo")
    replicas = mongodb_replicas()
    external_prefix = env_param("external_host_prefix", "domain-rs")
    nodeport_start = int(env_param("nodeport_start", "31181"))
    horizons = [f"{external_prefix}-{idx + 1}:{nodeport_start + idx}" for idx in range(replicas)]
    members = mongodb_members_js(prefix, service, namespace, replicas, horizons=horizons)
    mongodb_run(
        namespace,
        prefix,
        f"cfg=rs.conf(); cfg.members={members}; cfg.version=(cfg.version||1)+1; rs.reconfig(cfg,{{force:true}});",
    )
    mongodb_wait_primary(namespace, prefix)


def solve_mongodb_health_check_recovery() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    secret_name = env_param("health_secret_name", "health-user-password")
    current_password = mongodb_secret_value(namespace, secret_name)
    override_name = env_param("health_overrides_configmap_name", "health-overrides")
    mongodb_configmap(namespace, override_name, {f"{prefix}-1": current_password})
    run(["kubectl", "-n", namespace, "delete", "pod", f"{prefix}-1", "--ignore-not-found=true"])
    mongodb_wait_ready(namespace, prefix, mongodb_replicas(), timeout_sec=900)
    mongodb_wait_primary(namespace, prefix, uri=mongodb_admin_uri(namespace))


def solve_mongodb_manual_rbac_reset() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    app_db = env_param("app_database", "appdb")
    reports_collection = env_param("reports_collection", "reports")
    reporting_role = env_param("reporting_role_name", "reportingRole")
    bad_role = env_param("bad_role_name", "rawRead")
    app_user = env_param("app_username", "app-user")
    reporting_user = env_param("reporting_username", "reporting-user")
    admin_user = env_param("admin_username", "admin-user")
    app_secret = env_param("app_secret_name", "app-user-password")
    reporting_secret = env_param("reporting_secret_name", "reporting-user-password")
    configmap_name = env_param("reset_script_configmap_name", "mongodb-rbac-reset-script")
    script_key = env_param("reset_script_key", "reset_rbac.sh")
    script = f"""#!/bin/sh
set -eu
NS="${{BENCH_NAMESPACE:-{namespace}}}"
decode_secret() {{
  kubectl -n "$NS" get secret "$1" -o "jsonpath={{.data.password}}" | python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.read().strip()).decode())'
}}
ADMIN_PW="$(decode_secret {env_param('admin_secret_name', 'admin-user-password')})"
APP_PW="$(decode_secret {app_secret})"
REPORTING_PW="$(decode_secret {reporting_secret})"
POD="{prefix}-0"
mongo_admin() {{
  kubectl -n "$NS" exec "$POD" -- mongosh --quiet "mongodb://{admin_user}:$ADMIN_PW@localhost:27017/admin" --eval "$1"
}}
ensure_app() {{
  mongo_admin 'try {{ db.getSiblingDB("admin").createUser({{user:"{app_user}",pwd:"'"$APP_PW"'",roles:[{{role:"readWrite",db:"{app_db}"}}]}}); }} catch (e) {{ db.getSiblingDB("admin").updateUser("{app_user}", {{pwd:"'"$APP_PW"'",roles:[{{role:"readWrite",db:"{app_db}"}}]}}); }}'
}}
ensure_reporting() {{
  mongo_admin 'try {{ db.getSiblingDB("{app_db}").createRole({{role:"{reporting_role}", privileges:[{{resource:{{db:"{app_db}",collection:"{reports_collection}"}},actions:["find"]}}], roles:[]}}); }} catch (e) {{ db.getSiblingDB("{app_db}").updateRole("{reporting_role}", {{privileges:[{{resource:{{db:"{app_db}",collection:"{reports_collection}"}},actions:["find"]}}], roles:[]}}); }}'
  mongo_admin 'try {{ db.getSiblingDB("admin").createUser({{user:"{reporting_user}",pwd:"'"$REPORTING_PW"'",roles:[{{role:"{reporting_role}",db:"{app_db}"}}]}}); }} catch (e) {{ db.getSiblingDB("admin").updateUser("{reporting_user}", {{pwd:"'"$REPORTING_PW"'",roles:[{{role:"{reporting_role}",db:"{app_db}"}}]}}); }}'
  mongo_admin 'try {{ db.getSiblingDB("{app_db}").dropRole("{bad_role}"); }} catch (e) {{}}'
}}
MODE="${{1:-}}"
if [ "$MODE" = "--mode" ]; then
  MODE="${{2:-all}}"
fi
case "$MODE" in
  all) ensure_app; ensure_reporting ;;
  app) ensure_app ;;
  reporting) ensure_reporting ;;
  *) echo "unsupported mode: $MODE" >&2; exit 1 ;;
esac
"""
    mongodb_configmap(namespace, configmap_name, {script_key: script})


def solve_mongodb_mongod_config_update() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    service = mongodb_service()
    replicas = mongodb_replicas()
    target_log = env_param("target_log_level", "1")
    target_slow = env_param("target_slow_ms", "200")
    target_compressor = env_param("target_journal_compressor", "zlib")
    configmap_name = env_param("mongod_configmap_name", "mongod-config")
    config = "\n".join(
        [
            "storage:",
            "  dbPath: /data/db",
            "  wiredTiger:",
            "    engineConfig:",
            f"      journalCompressor: {target_compressor}",
            "net:",
            "  bindIpAll: true",
            "replication:",
            f"  replSetName: {env_param('replica_set_name', prefix)}",
            "security:",
            "  authorization: enabled",
            "  keyFile: /etc/mongo-keyfile/keyfile",
            "systemLog:",
            f"  verbosity: {target_log}",
            "operationProfiling:",
            "  mode: slowOp",
            f"  slowOpThresholdMs: {target_slow}",
        ]
    )
    mongodb_configmap(namespace, configmap_name, {"mongod.conf": config})
    mongodb_restart_sts(namespace, prefix)
    mongodb_wait_ready(namespace, prefix, replicas, timeout_sec=900)
    mongodb_wait_primary(
        namespace,
        prefix,
        uri=mongodb_replica_set_admin_uri(namespace, prefix, service, replicas),
    )


def solve_mongodb_password_rotation() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    app_secret = env_param("app_secret_name", "app-user-password")
    next_secret = env_param("app_next_secret_name", "app-user-password-next")
    app_user = env_param("app_username", "app-user")
    app_db = env_param("app_database", "appdb")
    next_password = mongodb_secret_value(namespace, next_secret)
    mongodb_create_or_update_user(
        namespace,
        prefix,
        app_user,
        next_password,
        [{"role": "readWrite", "db": app_db}],
    )
    payload = capture(
        [
            "kubectl",
            "-n",
            namespace,
            "create",
            "secret",
            "generic",
            app_secret,
            f"--from-literal=password={next_password}",
            "--dry-run=client",
            "-o",
            "yaml",
        ]
    )
    run(["kubectl", "-n", namespace, "apply", "--validate=false", "-f", "-"], input_text=payload)


def solve_mongodb_readiness_probe_tuning() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    service = mongodb_service()
    replicas = mongodb_replicas()
    patch_json(
        namespace,
        f"statefulset/{prefix}",
        [
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/readinessProbe/initialDelaySeconds",
                "value": int(env_param("tuned_readiness_initial_delay", "20")),
            },
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/readinessProbe/timeoutSeconds",
                "value": int(env_param("tuned_readiness_timeout", "5")),
            },
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/readinessProbe/failureThreshold",
                "value": int(env_param("tuned_readiness_failure_threshold", "6")),
            },
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/livenessProbe/initialDelaySeconds",
                "value": int(env_param("tuned_liveness_initial_delay", "120")),
            },
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/livenessProbe/timeoutSeconds",
                "value": int(env_param("tuned_liveness_timeout", "5")),
            },
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/livenessProbe/failureThreshold",
                "value": int(env_param("tuned_liveness_failure_threshold", "10")),
            },
        ],
    )
    mongodb_restart_sts(namespace, prefix)
    mongodb_wait_ready(namespace, prefix, replicas, timeout_sec=900)
    mongodb_wait_primary(
        namespace,
        prefix,
        uri=mongodb_replica_set_admin_uri(namespace, prefix, service, replicas),
    )


def solve_mongodb_replica_scaling() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    service = mongodb_service()
    target_replicas = int(env_param("target_replicas", "5"))
    mongodb_scale_sts(namespace, prefix, target_replicas)
    mongodb_reset_member_hosts(namespace, prefix, service, target_replicas, uri=mongodb_admin_uri(namespace))
    mongodb_wait_primary(namespace, prefix, uri=mongodb_admin_uri(namespace))


def solve_mongodb_statefulset_customization() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    service = env_param("headless_service_name", "mongodb-replica-svc")
    replicas = mongodb_replicas()
    label_key = env_param("template_label_key", "monitoring")
    label_value = env_param("template_label_value", "enabled")
    patch_json(
        namespace,
        f"statefulset/{prefix}",
        [
            {
                "op": "replace",
                "path": f"/spec/template/metadata/labels/{label_key}",
                "value": label_value,
            },
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/resources/requests/memory",
                "value": f"{env_param('min_request_memory_mi', '512')}Mi",
            },
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/resources/limits/memory",
                "value": f"{env_param('min_limit_memory_mi', '1024')}Mi",
            },
        ],
    )
    mongodb_wait_ready(namespace, prefix, replicas, timeout_sec=900)
    mongodb_wait_primary(
        namespace,
        prefix,
        uri=mongodb_replica_set_admin_uri(namespace, prefix, service, replicas),
    )


def solve_mongodb_user_management() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    app_db = env_param("app_database", "appdb")
    reports_collection = env_param("reports_collection", "reports")
    reporting_role = env_param("reporting_role_name", "reportingRole")
    app_user = env_param("app_username", "app-user")
    readonly_user = env_param("readonly_username", "readonly-user")
    app_password = mongodb_secret_value(namespace, env_param("app_secret_name", "app-user-password"))
    readonly_password = mongodb_secret_value(namespace, env_param("readonly_secret_name", "readonly-user-password"))
    mongodb_create_or_update_role(
        namespace,
        prefix,
        reporting_role,
        [
            {
                "resource": {"db": app_db, "collection": reports_collection},
                "actions": ["find"],
            }
        ],
        db=app_db,
    )
    mongodb_create_or_update_user(
        namespace,
        prefix,
        app_user,
        app_password,
        [{"role": "readWrite", "db": app_db}],
    )
    mongodb_create_or_update_user(
        namespace,
        prefix,
        readonly_user,
        readonly_password,
        [{"role": "read", "db": app_db}, {"role": reporting_role, "db": app_db}],
    )


def solve_mongodb_custom_roles() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    app_db = env_param("app_database", "appdb")
    reports_collection = env_param("reports_collection", "reports")
    reporting_role = env_param("reporting_role_name", "reportingRole")
    reporting_user = env_param("reporting_username", "reporting-user")
    reporting_password = mongodb_secret_value(namespace, env_param("reporting_secret_name", "reporting-user-password"))
    mongodb_create_or_update_role(
        namespace,
        prefix,
        reporting_role,
        [
            {
                "resource": {"db": app_db, "collection": reports_collection},
                "actions": ["find"],
            }
        ],
        db=app_db,
    )
    mongodb_create_or_update_user(
        namespace,
        prefix,
        reporting_user,
        reporting_password,
        [{"role": reporting_role, "db": app_db}],
    )
    mongodb_drop_role(namespace, prefix, env_param("bad_role_name", "rawRead"), db=app_db)


def solve_mongodb_version_upgrade() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    service = mongodb_service()
    replicas = mongodb_replicas()
    to_image = env_param("to_image", "mongo:7.0.5")
    to_fcv = env_param("to_fcv", "7.0")
    set_workload_container_image(namespace, f"statefulset/{prefix}", "mongod", to_image)
    rollout_status(namespace, f"statefulset/{prefix}", timeout="1200s")
    mongodb_wait_ready(namespace, prefix, replicas, timeout_sec=1200)
    admin_uri = mongodb_replica_set_admin_uri(namespace, prefix, service, replicas)
    mongodb_wait_primary(namespace, prefix, uri=admin_uri, timeout_sec=300)
    mongodb_run(
        namespace,
        prefix,
        f'db.adminCommand({{setFeatureCompatibilityVersion:{json.dumps(to_fcv)}}})',
        uri=admin_uri,
    )
    wait_until(
        lambda: mongodb_capture(
            namespace,
            prefix,
            "db.adminCommand({getParameter:1,featureCompatibilityVersion:1}).featureCompatibilityVersion.version",
            uri=admin_uri,
        ).strip().strip('"')
        == to_fcv,
        timeout_sec=180,
        interval_sec=5,
        err="mongodb FCV did not reach target version",
    )


def solve_mongodb_tls_setup() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix("cluster_prefix", "mongo-rs")
    service = env_param("service_name", "mongo")
    replicas = mongodb_replicas()
    ca_secret = env_param("tls_ca_secret_name", "mongodb-tls-ca")
    cert_secret = env_param("tls_cert_secret_name", "mongodb-tls-cert")
    image = env_param("mongo_image", "mongo:6.0")
    with tempfile.TemporaryDirectory() as td:
        bundle = mongodb_write_server_certificate_bundle(
            Path(td),
            hosts=mongodb_tls_dns_names(namespace, prefix, service, replicas),
            common_name=prefix,
            valid_days=365,
        )
        create_or_apply_secret(namespace, ca_secret, {"ca.crt": bundle["ca.crt"], "ca.key": bundle["ca.key"]})
        create_or_apply_secret(namespace, cert_secret, {"server.pem": bundle["server.pem"]})
    replace_json(
        namespace,
        f"statefulset/{prefix}",
        {
            "spec": {
                "serviceName": service,
                "replicas": replicas,
                "template": {
                    "metadata": {"labels": {"app": prefix}},
                    "spec": {
                        "containers": [
                            {
                                "name": "mongod",
                                "image": image,
                                "command": [
                                    "mongod",
                                    "--replSet",
                                    env_param("replica_set_name", "rs0"),
                                    "--bind_ip_all",
                                    "--tlsMode",
                                    "requireTLS",
                                    "--tlsCertificateKeyFile",
                                    "/etc/mongo-cert/server.pem",
                                    "--tlsCAFile",
                                    "/etc/mongo-ca/ca.crt",
                                    "--tlsAllowConnectionsWithoutCertificates",
                                ],
                                "ports": [{"name": "mongodb", "containerPort": 27017}],
                                "volumeMounts": [
                                    {"name": "data", "mountPath": "/data/db"},
                                    {"name": "mongo-tls-cert", "mountPath": "/etc/mongo-cert", "readOnly": True},
                                    {"name": "mongo-tls-ca", "mountPath": "/etc/mongo-ca", "readOnly": True},
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "mongo-tls-cert", "secret": {"secretName": cert_secret}},
                            {"name": "mongo-tls-ca", "secret": {"secretName": ca_secret}},
                        ],
                    },
                },
            }
        },
    )
    mongodb_wait_ready(namespace, prefix, replicas, timeout_sec=1200)
    mongodb_wait_tls_primary(namespace, prefix, service, timeout_sec=600)


def solve_mongodb_certificate_rotation() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix("cluster_prefix", "mongo-rs")
    service = env_param("service_name", "mongo")
    replicas = mongodb_replicas()
    ca_secret = env_param("tls_ca_secret_name", "mongodb-tls-ca")
    cert_secret = env_param("tls_cert_secret_name", "mongodb-tls-cert")
    ca_data = decode_secret_data(namespace, ca_secret)
    with tempfile.TemporaryDirectory() as td:
        bundle = mongodb_write_server_certificate_bundle(
            Path(td),
            hosts=mongodb_tls_dns_names(namespace, prefix, service, replicas),
            common_name=prefix,
            valid_days=int(env_param("target_validity_days", "365")),
            ca_cert=ca_data["ca.crt"],
            ca_key=ca_data["ca.key"],
        )
        create_or_apply_secret(namespace, cert_secret, {"server.pem": bundle["server.pem"]})
    mongodb_restart_sts(namespace, prefix)
    mongodb_wait_ready(namespace, prefix, replicas, timeout_sec=1200)
    mongodb_wait_tls_primary(namespace, prefix, service, timeout_sec=600)


def solve_mongodb_monitoring_integration() -> None:
    namespace = mongodb_namespace()
    monitoring_ns = namespace_alias("monitoring")
    prefix = mongodb_prefix("cluster_prefix", "mongo-rs")
    service = env_param("service_name", "mongo")
    exporter_name = env_param("exporter_deployment_name", "mongodb-exporter")
    exporter_service = env_param("exporter_service_name", "mongodb-exporter")
    metrics_port = int(env_param("metrics_port", "9216"))
    metrics_path = env_param("metrics_path", "/metrics")
    prometheus_name = env_param("prometheus_deployment_name", "prometheus")
    prometheus_configmap = env_param("prometheus_configmap_name", "prometheus-config")
    exporter_uri = (
        f"mongodb://{prefix}-0.{service}.{namespace}.svc.cluster.local:27017/admin"
        "?directConnection=true&serverSelectionTimeoutMS=4000&connectTimeoutMS=4000"
    )
    exporter_yaml = f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {exporter_name}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {exporter_name}
  template:
    metadata:
      labels:
        app: {exporter_name}
    spec:
      containers:
      - name: exporter
        image: percona/mongodb_exporter:0.40.0
        args:
        - --mongodb.uri={exporter_uri}
        ports:
        - name: metrics
          containerPort: {metrics_port}
---
apiVersion: v1
kind: Service
metadata:
  name: {exporter_service}
spec:
  selector:
    app: {exporter_name}
  ports:
  - name: metrics
    port: {metrics_port}
    targetPort: {metrics_port}
"""
    prometheus_cfg = "\n".join(
        [
            "global:",
            "  scrape_interval: 30s",
            "scrape_configs:",
            '  - job_name: "prometheus"',
            "    static_configs:",
            '      - targets: ["localhost:9090"]',
            '  - job_name: "mongodb"',
            f"    metrics_path: {metrics_path}",
            "    static_configs:",
            f'      - targets: ["{exporter_service}.{namespace}.svc:{metrics_port}"]',
            "",
        ]
    )
    apply_yaml(exporter_yaml, namespace=namespace)
    rollout_status(namespace, f"deployment/{exporter_name}", timeout="600s")
    create_or_apply_configmap(monitoring_ns, prometheus_configmap, {"prometheus.yml": prometheus_cfg})
    run(["kubectl", "-n", monitoring_ns, "rollout", "restart", f"deployment/{prometheus_name}"])
    rollout_status(monitoring_ns, f"deployment/{prometheus_name}", timeout="600s")
    wait_until(
        lambda: run_ok(["python3", "resources/mongodb/monitoring-integration/oracle/oracle.py"]),
        timeout_sec=600,
        interval_sec=5,
        err="mongodb monitoring integration did not converge",
    )


def solve_mongodb_setup_rbac_drift_app() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    app_db = env_param("app_database", "appdb")
    reporting_role = env_param("reporting_role_name", "reportingRole")
    app_user = env_param("app_username", "app-user")
    readonly_user = env_param("readonly_username", "readonly-user")
    app_password = mongodb_secret_value(namespace, env_param("app_secret_name", "app-user-password"))
    readonly_password = mongodb_secret_value(namespace, env_param("readonly_secret_name", "readonly-user-password"))
    mongodb_create_or_update_user(
        namespace,
        prefix,
        app_user,
        app_password,
        [{"role": "read", "db": app_db}],
    )
    mongodb_create_or_update_user(
        namespace,
        prefix,
        readonly_user,
        readonly_password,
        [{"role": "read", "db": app_db}],
    )
    mongodb_drop_role(namespace, prefix, reporting_role, db="admin")
    mongodb_drop_role(namespace, prefix, reporting_role, db=app_db)


def solve_mongodb_setup_rbac_drift_reporting() -> None:
    namespace = mongodb_namespace()
    prefix = mongodb_prefix()
    app_db = env_param("app_database", "appdb")
    raw_collection = env_param("raw_collection", "raw")
    bad_role = env_param("bad_role_name", "rawRead")
    reporting_role = env_param("reporting_role_name", "reportingRole")
    reporting_user = env_param("reporting_username", "reporting-user")
    reporting_password = mongodb_secret_value(namespace, env_param("reporting_secret_name", "reporting-user-password"))
    mongodb_create_or_update_role(
        namespace,
        prefix,
        bad_role,
        [
            {
                "resource": {"db": app_db, "collection": raw_collection},
                "actions": ["find"],
            }
        ],
        db=app_db,
    )
    mongodb_create_or_update_user(
        namespace,
        prefix,
        reporting_user,
        reporting_password,
        [{"role": bad_role, "db": app_db}],
    )
    mongodb_drop_role(namespace, prefix, reporting_role, db="admin")
    mongodb_drop_role(namespace, prefix, reporting_role, db=app_db)


def apply_yaml(payload: str, *, namespace: str | None = None) -> None:
    cmd = ["kubectl"]
    if namespace:
        cmd.extend(["-n", namespace])
    cmd.extend(["apply", "--validate=false", "-f", "-"])
    run(cmd, input_text=payload)


def create_or_apply_secret_literals(namespace: str, name: str, literals: dict[str, str]) -> None:
    cmd = ["kubectl", "-n", namespace, "create", "secret", "generic", name]
    for key, value in literals.items():
        cmd.append(f"--from-literal={key}={value}")
    cmd.extend(["--dry-run=client", "-o", "yaml"])
    payload = capture(cmd)
    run(["kubectl", "-n", namespace, "apply", "--validate=false", "-f", "-"], input_text=payload)


def create_or_apply_configmap_files(namespace: str, name: str, files: dict[str, Path]) -> None:
    cmd = ["kubectl", "-n", namespace, "create", "configmap", name]
    for key, path in files.items():
        cmd.append(f"--from-file={key}={path}")
    cmd.extend(["--dry-run=client", "-o", "yaml"])
    payload = capture(cmd)
    run(["kubectl", "-n", namespace, "apply", "--validate=false", "-f", "-"], input_text=payload)


def elastic_namespace() -> str:
    return env_required("BENCH_NAMESPACE")


def elastic_monitoring_namespace() -> str:
    return os.environ.get("BENCH_NS_MONITORING", "").strip() or elastic_namespace()


def elastic_cluster_prefix(default: str = "es-cluster") -> str:
    return env_param("cluster_prefix", default)


def elastic_http_service(default: str = "es-http") -> str:
    return env_param("http_service_name", default)


def elastic_curl_pod(default: str = "curl-test") -> str:
    return env_param("curl_pod_name", default)


def elastic_exec(namespace: str, pod: str, args: list[str]) -> None:
    run(["kubectl", "-n", namespace, "exec", pod, "--"] + args)


def elastic_capture(namespace: str, pod: str, args: list[str]) -> str:
    return capture(["kubectl", "-n", namespace, "exec", pod, "--"] + args)


def secret_text(namespace: str, name: str, key: str, default: str | None = None) -> str:
    data = decode_secret_data(namespace, name)
    if key in data:
        return data[key].decode("utf-8")
    if default is not None:
        return default
    raise RuntimeError(f"secret/{name} missing key {key}")


def decode_configmap_text(namespace: str, name: str, key: str) -> str:
    payload = json.loads(
        capture(["kubectl", "-n", namespace, "get", "configmap", name, "-o", "json"])
    )
    data = payload.get("data") or {}
    value = data.get(key)
    if value is None:
        raise RuntimeError(f"configmap/{name} missing key {key}")
    return value


def elastic_password(secret_name: str = "elastic-password", key: str = "password") -> str:
    namespace = elastic_namespace()
    value = capture_maybe(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "secret",
            secret_name,
            "-o",
            f"jsonpath={{.data.{key}}}",
        ]
    )
    if value:
        return base64.b64decode(value.strip()).decode("utf-8")
    return env_param("elastic_password", "elasticpass")


def elastic_apply_dir(relative_dir: str, namespace: str) -> None:
    run(["kubectl", "-n", namespace, "apply", "-f", relative_dir])


def elastic_delete_pods(namespace: str, selector: str) -> None:
    run(["kubectl", "-n", namespace, "delete", "pod", "-l", selector, "--wait=false", "--ignore-not-found=true"])


def elastic_wait_statefulset(namespace: str, name: str, expected: int, *, timeout_sec: int = 1200) -> None:
    wait_statefulset_ready(namespace, name, expected, timeout_sec=timeout_sec)
    wait_until(
        lambda: run_ok(
            ["kubectl", "-n", namespace, "wait", "--for=condition=ready", "pod", "-l", f"app={name}", "--timeout=10s"]
        ),
        timeout_sec=timeout_sec,
        interval_sec=5,
        err=f"pods for statefulset/{name} did not become ready",
    )


def elastic_wait_deployment(namespace: str, name: str, *, timeout: str = "600s") -> None:
    rollout_status(namespace, f"deployment/{name}", timeout=timeout)


def elastic_curl(
    namespace: str,
    pod: str,
    service: str,
    path: str,
    *,
    port: int = 9200,
    https: bool = False,
    auth: tuple[str, str] | None = None,
    method: str = "GET",
    data: str | None = None,
    headers: dict[str, str] | None = None,
    host_header: str | None = None,
    output_code: bool = False,
) -> str:
    cmd = ["kubectl", "-n", namespace, "exec", pod, "--", "curl", "-s", "-S", "--max-time", "10"]
    if https:
        cmd.append("-k")
    if output_code:
        cmd.extend(["-o", "/dev/null", "-w", "%{http_code}"])
    if auth:
        cmd.extend(["-u", f"{auth[0]}:{auth[1]}"])
    if method and method != "GET":
        cmd.extend(["-X", method])
    for key, value in (headers or {}).items():
        cmd.extend(["-H", f"{key}: {value}"])
    if host_header:
        cmd.extend(["-H", f"Host: {host_header}"])
    if data is not None:
        cmd.extend(["-d", data])
    scheme = "https" if https else "http"
    cmd.append(f"{scheme}://{service}:{port}{path}")
    proc = subprocess.run(
        cmd,
        text=True,
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"curl failed for {service}{path}: {detail}")
    return proc.stdout.strip()


def elastic_json(
    namespace: str,
    pod: str,
    service: str,
    path: str,
    *,
    https: bool = False,
    auth: tuple[str, str] | None = None,
    method: str = "GET",
    data: str | None = None,
    headers: dict[str, str] | None = None,
    host_header: str | None = None,
) -> dict | list:
    raw = elastic_curl(
        namespace,
        pod,
        service,
        path,
        https=https,
        auth=auth,
        method=method,
        data=data,
        headers=headers,
        host_header=host_header,
    )
    return json.loads(raw)


def elastic_wait_health(
    namespace: str,
    service: str,
    *,
    expected_nodes: int,
    pod: str | None = None,
    https: bool = False,
    auth: tuple[str, str] | None = None,
    timeout_sec: int = 900,
) -> None:
    curl_pod = pod or elastic_curl_pod()

    def healthy() -> bool:
        try:
            health = elastic_json(
                namespace,
                curl_pod,
                service,
                f"/_cluster/health?wait_for_status=yellow&wait_for_nodes={expected_nodes}&timeout=10s",
                https=https,
                auth=auth,
            )
        except Exception:
            return False
        return (
            isinstance(health, dict)
            and health.get("status") in {"yellow", "green"}
            and int(health.get("number_of_nodes") or 0) == expected_nodes
        )

    wait_until(
        healthy,
        timeout_sec=timeout_sec,
        interval_sec=5,
        err=f"elasticsearch health for {service} did not reach {expected_nodes} nodes",
    )


def elastic_patch_config(namespace: str, text: str, *, name: str = "es-config") -> None:
    replace_json(namespace, f"configmap/{name}", {"data": {"elasticsearch.yml": text}})


def elastic_restart_statefulset(namespace: str, name: str, *, expected: int, timeout_sec: int = 1200) -> None:
    run(["kubectl", "-n", namespace, "rollout", "restart", f"statefulset/{name}"])
    elastic_wait_statefulset(namespace, name, expected, timeout_sec=timeout_sec)


def elastic_generate_http_bundle(hosts: list[str], *, common_name: str, days: int) -> dict[str, Path]:
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        san_lines = "\n".join(
            f"DNS.{index + 1}={host}" for index, host in enumerate(hosts)
        )
        cnf = "\n".join(
            [
                "distinguished_name=req_distinguished_name",
                "req_extensions=v3_req",
                "prompt=no",
                "[req_distinguished_name]",
                f"CN={common_name}",
                "[v3_req]",
                "keyUsage=critical,digitalSignature,keyEncipherment",
                "extendedKeyUsage=serverAuth,clientAuth",
                "subjectAltName=@alt_names",
                "[alt_names]",
                san_lines,
            ]
        )
        (td_path / "openssl.cnf").write_text(cnf, encoding="utf-8")
        run(["openssl", "genrsa", "-out", str(td_path / "ca.key"), "2048"])
        run(
            [
                "openssl",
                "req",
                "-x509",
                "-new",
                "-nodes",
                "-key",
                str(td_path / "ca.key"),
                "-sha256",
                "-days",
                str(days),
                "-subj",
                f"/CN={common_name}-ca",
                "-out",
                str(td_path / "ca.crt"),
            ]
        )
        run(["openssl", "genrsa", "-out", str(td_path / "server.key"), "2048"])
        run(
            [
                "openssl",
                "req",
                "-new",
                "-key",
                str(td_path / "server.key"),
                "-out",
                str(td_path / "server.csr"),
                "-config",
                str(td_path / "openssl.cnf"),
            ]
        )
        run(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(td_path / "server.csr"),
                "-CA",
                str(td_path / "ca.crt"),
                "-CAkey",
                str(td_path / "ca.key"),
                "-CAcreateserial",
                "-out",
                str(td_path / "server.crt"),
                "-days",
                str(days),
                "-extensions",
                "v3_req",
                "-extfile",
                str(td_path / "openssl.cnf"),
            ]
        )
        out_dir = Path(tempfile.mkdtemp())
        files = {}
        for src_name, dest_name in (("server.crt", "tls.crt"), ("server.key", "tls.key"), ("ca.crt", "ca.crt")):
            dest = out_dir / dest_name
            dest.write_bytes((td_path / src_name).read_bytes())
            files[dest_name] = dest
        return files


def elastic_merge_unique_lines(*payloads: str) -> str:
    seen: set[str] = set()
    merged: list[str] = []
    for payload in payloads:
        for line in payload.splitlines():
            value = line.rstrip()
            if not value or value in seen:
                continue
            seen.add(value)
            merged.append(value)
    return "\n".join(merged) + ("\n" if merged else "")


def elastic_insecure_config(
    *,
    cluster_name: str,
    seed_hosts: list[str],
    include_bootstrap: bool = True,
    node_roles: str = "[ master, data, ingest ]",
    extra_lines: list[str] | None = None,
) -> str:
    lines = [
        f"cluster.name: {cluster_name}",
        "node.name: ${POD_NAME}",
        f"node.roles: {node_roles}",
        "network.host: 0.0.0.0",
        "discovery.seed_hosts:",
    ]
    lines.extend(f"  - {host}" for host in seed_hosts)
    if include_bootstrap:
        lines.append("cluster.initial_master_nodes:")
        lines.extend(f"  - {host.split('.')[0]}" for host in seed_hosts)
    lines.extend(
        [
            "node.store.allow_mmap: false",
            "xpack.security.enabled: false",
            "xpack.security.http.ssl.enabled: false",
            "xpack.security.transport.ssl.enabled: false",
        ]
    )
    lines.extend(extra_lines or [])
    return "\n".join(lines)


def elastic_seed_hosts(prefix: str) -> list[str]:
    return [f"{prefix}-{idx}.{prefix}" for idx in range(3)]


def solve_elasticsearch_bootstrap_initial_master_nodes() -> None:
    namespace = elastic_namespace()
    prefix = elastic_cluster_prefix()
    service = elastic_http_service()
    desired = elastic_insecure_config(
        cluster_name=prefix,
        seed_hosts=elastic_seed_hosts(prefix),
        include_bootstrap=True,
    )
    elastic_patch_config(namespace, desired)
    elastic_delete_pods(namespace, f"app={prefix}")
    elastic_wait_statefulset(namespace, prefix, 3, timeout_sec=1200)
    elastic_wait_health(namespace, service, expected_nodes=3, timeout_sec=1200)
    stable = elastic_insecure_config(
        cluster_name=prefix,
        seed_hosts=elastic_seed_hosts(prefix),
        include_bootstrap=False,
    )
    elastic_patch_config(namespace, stable)
    elastic_delete_pods(namespace, f"app={prefix}")
    elastic_wait_statefulset(namespace, prefix, 3, timeout_sec=1200)
    elastic_wait_health(namespace, service, expected_nodes=3, timeout_sec=1200)


def solve_elasticsearch_deploy_core_cluster() -> None:
    namespace = elastic_namespace()
    prefix = elastic_cluster_prefix()
    service = elastic_http_service()
    target_image = env_param("target_image", "docker.elastic.co/elasticsearch/elasticsearch:8.11.1")
    if not run_ok(["kubectl", "-n", namespace, "get", "configmap", "es-config"]):
        apply_env_template("resources/elasticsearch/bootstrap-initial-master-nodes/resource/configmap.yaml", namespace=namespace)
    if not run_ok(["kubectl", "-n", namespace, "get", "service", prefix]):
        apply_env_template("resources/elasticsearch/bootstrap-initial-master-nodes/resource/service-headless.yaml", namespace=namespace)
    if not run_ok(["kubectl", "-n", namespace, "get", "service", service]):
        apply_env_template("resources/elasticsearch/bootstrap-initial-master-nodes/resource/service-http.yaml", namespace=namespace)
    if not run_ok(["kubectl", "-n", namespace, "get", "statefulset", prefix]):
        apply_env_template("resources/elasticsearch/bootstrap-initial-master-nodes/resource/statefulset.yaml", namespace=namespace)
    desired = elastic_insecure_config(
        cluster_name=prefix,
        seed_hosts=elastic_seed_hosts(prefix),
        include_bootstrap=True,
    )
    elastic_patch_config(namespace, desired)
    set_workload_container_image(namespace, f"statefulset/{prefix}", "elasticsearch", target_image)
    rollout_status(namespace, f"statefulset/{prefix}", timeout="1200s")
    elastic_wait_statefulset(namespace, prefix, 3, timeout_sec=1200)
    elastic_wait_health(namespace, service, expected_nodes=3, timeout_sec=1200)


def solve_elasticsearch_file_realm_user_roles_merge() -> None:
    namespace = elastic_namespace()
    prefix = elastic_cluster_prefix()
    service = elastic_http_service()
    aggregate_name = env_param("aggregate_secret_name", "es-file-realm-aggregate")
    provided_name = env_param("provided_secret_name", "user-provided-file-realm")
    ops_user = env_param("ops_user", "ops-user")
    ops_pass = env_param("ops_password", "opspass")
    aggregate = decode_secret_data(namespace, aggregate_name)
    provided = decode_secret_data(namespace, provided_name)
    merged_users = elastic_merge_unique_lines(
        aggregate.get("users", b"").decode("utf-8"),
        provided.get("users", b"").decode("utf-8"),
    )
    merged_users_roles = elastic_merge_unique_lines(
        aggregate.get("users_roles", b"").decode("utf-8"),
        provided.get("users_roles", b"").decode("utf-8"),
    )
    merged_roles = "\n".join(
        part.strip()
        for part in (
            aggregate.get("roles.yml", b"").decode("utf-8"),
            provided.get("roles.yml", b"").decode("utf-8"),
        )
        if part.strip()
    )
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        files = {}
        for key, payload in {
            "users": merged_users,
            "users_roles": merged_users_roles,
            "roles.yml": merged_roles + ("\n" if merged_roles else ""),
        }.items():
            path = td_path / key
            path.write_text(payload, encoding="utf-8")
            files[key] = path
        create_or_apply_secret(namespace, aggregate_name, files)
    elastic_delete_pods(namespace, f"app={prefix}")
    elastic_wait_statefulset(namespace, prefix, 3, timeout_sec=1200)
    elastic_wait_health(
        namespace,
        service,
        expected_nodes=3,
        https=True,
        auth=(ops_user, ops_pass),
        timeout_sec=1200,
    )


def solve_elasticsearch_full_restart_upgrade_ha() -> None:
    namespace = elastic_namespace()
    prefix = elastic_cluster_prefix()
    service = elastic_http_service()
    to_version = env_param("to_version", "8.11.1")
    to_image = f"docker.elastic.co/elasticsearch/elasticsearch:{to_version}"
    run(["kubectl", "-n", namespace, "scale", f"statefulset/{prefix}", "--replicas=0"])
    wait_until(
        lambda: statefulset_ready_replicas(namespace, prefix) == 0,
        timeout_sec=600,
        interval_sec=5,
        err=f"statefulset/{prefix} did not scale down",
    )
    set_workload_container_image(namespace, f"statefulset/{prefix}", "elasticsearch", to_image)
    replace_json(namespace, f"statefulset/{prefix}", {"spec": {"replicas": 3}})
    elastic_wait_statefulset(namespace, prefix, 3, timeout_sec=1200)
    elastic_wait_health(namespace, service, expected_nodes=3, timeout_sec=1200)


def solve_elasticsearch_internal_http_service_drift() -> None:
    namespace = elastic_namespace()
    prod_prefix = env_param("prod_cluster_prefix", "es-alpha")
    prod_service = env_param("prod_service_name", "search-http")
    replace_json(namespace, f"service/{prod_service}", {"spec": {"selector": {"app": prod_prefix}}})
    elastic_wait_deployment(namespace, env_param("log_reader_deployment", "log-reader"), timeout="300s")


def solve_elasticsearch_master_downscale_voting_exclusions() -> None:
    namespace = elastic_namespace()
    prefix = elastic_cluster_prefix()
    service = elastic_http_service()
    pod = elastic_curl_pod()
    run(["kubectl", "-n", namespace, "scale", f"statefulset/{prefix}", "--replicas=3"])
    elastic_wait_statefulset(namespace, prefix, 3, timeout_sec=1200)
    elastic_wait_health(namespace, service, expected_nodes=3, timeout_sec=600)
    elastic_json(
        namespace,
        pod,
        service,
        "/_cluster/settings",
        method="PUT",
        data='{"persistent":{"cluster.auto_shrink_voting_configuration":true,"ingest.geoip.downloader.enabled":false}}',
        headers={"Content-Type": "application/json"},
    )
    elastic_curl(
        namespace,
        pod,
        service,
        f"/_cluster/voting_config_exclusions?node_names={prefix}-1,{prefix}-2&timeout=60s",
        method="POST",
    )
    run(["kubectl", "-n", namespace, "scale", f"statefulset/{prefix}", "--replicas=1"])
    elastic_wait_statefulset(namespace, prefix, 1, timeout_sec=1200)
    wait_until(
        lambda: isinstance(
            (health := elastic_json(namespace, pod, service, "/_cluster/health?local=true")),
            dict,
        )
        and int(health.get("number_of_nodes") or 0) == 1,
        timeout_sec=300,
        interval_sec=5,
        err="elasticsearch local health did not report a single-node cluster",
    )
    for path in (
        "/_data_stream/ilm-history-5?expand_wildcards=all",
        "/.geoip_databases?expand_wildcards=all",
    ):
        try:
            elastic_curl(namespace, pod, service, path, method="DELETE")
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise
    elastic_curl(namespace, pod, service, "/_cluster/voting_config_exclusions?wait_for_removal=false", method="DELETE")
    elastic_wait_health(namespace, service, expected_nodes=1, timeout_sec=600)


def solve_elasticsearch_rotate_elastic_password() -> None:
    namespace = elastic_namespace()
    service = elastic_http_service()
    pod = elastic_curl_pod()
    current_secret = env_param("current_password_secret_name", "elastic-password")
    next_secret = env_param("next_password_secret_name", "elastic-password-next")
    auth_checker = env_param("auth_checker_deployment_name", "auth-checker")
    current_pw = secret_text(namespace, current_secret, "password", default="elasticpass-old")
    next_pw = secret_text(namespace, next_secret, "password", default="elasticpass")
    elastic_json(
        namespace,
        pod,
        service,
        "/_security/user/elastic/_password",
        method="POST",
        data=json.dumps({"password": next_pw}),
        headers={"Content-Type": "application/json"},
        auth=("elastic", current_pw),
    )
    create_or_apply_secret_literals(namespace, current_secret, {"password": next_pw})
    run(["kubectl", "-n", namespace, "rollout", "restart", f"deployment/{auth_checker}"])
    elastic_wait_deployment(namespace, auth_checker, timeout="600s")


def solve_elasticsearch_rotate_http_certs() -> None:
    namespace = elastic_namespace()
    prefix = elastic_cluster_prefix()
    service = elastic_http_service()
    secret_name = env_param("tls_secret_name", "es-http-tls")
    ca_cm = env_param("http_ca_configmap_name", "es-http-ca")
    bundle = elastic_generate_http_bundle(
        ["localhost", service, prefix, "*.svc", "*.svc.cluster.local"],
        common_name=service,
        days=365,
    )
    create_or_apply_secret(namespace, secret_name, bundle)
    create_or_apply_configmap_files(namespace, ca_cm, {"ca.crt": bundle["ca.crt"]})
    elastic_delete_pods(namespace, f"app={prefix}")
    elastic_wait_statefulset(namespace, prefix, 3, timeout_sec=1200)
    elastic_wait_health(
        namespace,
        service,
        expected_nodes=3,
        https=True,
        auth=(env_param("elastic_username", "elastic"), env_param("elastic_password", "elasticpass")),
        timeout_sec=1200,
    )


def solve_elasticsearch_safe_downscale_with_shard_migration() -> None:
    namespace = elastic_namespace()
    prefix = elastic_cluster_prefix()
    service = elastic_http_service()
    pod = elastic_curl_pod()
    index_name = env_param("index_name", "app-data")
    elastic_json(
        namespace,
        pod,
        service,
        "/_cluster/settings",
        method="PUT",
        data='{"persistent":{"cluster.auto_shrink_voting_configuration":true}}',
        headers={"Content-Type": "application/json"},
    )
    elastic_json(
        namespace,
        pod,
        service,
        f"/{index_name}/_settings",
        method="PUT",
        data=json.dumps(
            {
                "index": {
                    "number_of_replicas": 0,
                    "routing": {"allocation": {"include": {"_name": f"{prefix}-0"}}},
                }
            }
        ),
        headers={"Content-Type": "application/json"},
    )
    wait_until(
        lambda: isinstance(
            (shards := elastic_json(namespace, pod, service, f"/_cat/shards/{index_name}?format=json")),
            list,
        )
        and all(item.get("node") == f"{prefix}-0" for item in shards),
        timeout_sec=600,
        interval_sec=5,
        err="shards did not migrate to remaining node",
    )
    elastic_curl(
        namespace,
        pod,
        service,
        f"/_cluster/voting_config_exclusions?node_names={prefix}-1,{prefix}-2&timeout=60s",
        method="POST",
    )
    run(["kubectl", "-n", namespace, "scale", f"statefulset/{prefix}", "--replicas=1"])
    elastic_wait_statefulset(namespace, prefix, 1, timeout_sec=1200)
    wait_until(
        lambda: isinstance(
            (health := elastic_json(namespace, pod, service, "/_cluster/health?local=true")),
            dict,
        )
        and int(health.get("number_of_nodes") or 0) == 1,
        timeout_sec=300,
        interval_sec=5,
        err="elasticsearch local health did not report single-node cluster",
    )
    try:
        elastic_curl(
            namespace,
            pod,
            service,
            "/.geoip_databases?expand_wildcards=all",
            method="DELETE",
        )
    except RuntimeError as exc:
        if "404" not in str(exc):
            raise
    for ordinal in (1, 2):
        run(["kubectl", "-n", namespace, "delete", "pvc", f"data-{prefix}-{ordinal}", "--ignore-not-found=true"])
    elastic_curl(namespace, pod, service, "/_cluster/voting_config_exclusions?wait_for_removal=false", method="DELETE")
    elastic_wait_health(namespace, service, expected_nodes=1, timeout_sec=600)


def solve_elasticsearch_scale_up_new_nodeset() -> None:
    namespace = elastic_namespace()
    service = elastic_http_service()
    pod = elastic_curl_pod()
    extra_name = "es-scale-extra"
    extra_cm = "es-scale-extra-config"
    config = elastic_insecure_config(
        cluster_name=elastic_cluster_prefix(),
        seed_hosts=elastic_seed_hosts(elastic_cluster_prefix()),
        include_bootstrap=False,
        node_roles="[ data, ingest ]",
        extra_lines=["node.attr.tier: warm"],
    )
    apply_yaml(
        f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {extra_cm}
data:
  elasticsearch.yml: |
{chr(10).join('    ' + line for line in config.splitlines())}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {extra_name}
spec:
  replicas: 2
  selector:
    matchLabels:
      app: {extra_name}
  template:
    metadata:
      labels:
        app: {extra_name}
    spec:
      containers:
      - name: elasticsearch
        image: docker.elastic.co/elasticsearch/elasticsearch:8.11.1
        env:
        - name: POD_NAME
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: ES_JAVA_OPTS
          value: -Xms512m -Xmx512m
        ports:
        - containerPort: 9200
          name: http
        - containerPort: 9300
          name: transport
        readinessProbe:
          httpGet:
            path: /_cluster/health?local=true
            port: http
          initialDelaySeconds: 10
          periodSeconds: 5
          timeoutSeconds: 2
          failureThreshold: 6
        volumeMounts:
        - name: es-config
          mountPath: /usr/share/elasticsearch/config/elasticsearch.yml
          subPath: elasticsearch.yml
        - name: data
          mountPath: /usr/share/elasticsearch/data
      volumes:
      - name: es-config
        configMap:
          name: {extra_cm}
          items:
          - key: elasticsearch.yml
            path: elasticsearch.yml
      - name: data
        emptyDir: {{}}
""",
        namespace=namespace,
    )
    elastic_wait_deployment(namespace, extra_name, timeout="1200s")
    elastic_wait_health(namespace, service, expected_nodes=5, timeout_sec=1200)
    index_name = env_param("index_name", "app-data")
    elastic_json(
        namespace,
        pod,
        service,
        f"/{index_name}/_settings",
        method="PUT",
        data='{"index.routing.allocation.require.tier":"warm"}',
        headers={"Content-Type": "application/json"},
    )
    wait_until(
        lambda: isinstance(
            (shards := elastic_json(namespace, pod, service, f"/_cat/shards/{index_name}?format=json")),
            list,
        )
        and any(str(item.get("node", "")).startswith(f"{extra_name}-") for item in shards),
        timeout_sec=600,
        interval_sec=5,
        err="index shards did not move onto new nodes",
    )


def solve_elasticsearch_secure_http_ingress() -> None:
    namespace = elastic_namespace()
    ingress_ns = os.environ.get("BENCH_NS_INGRESS") or env_param("ingress_namespace", "ingress-nginx")
    service = elastic_http_service()
    prefix = elastic_cluster_prefix()
    secret_name = env_param("tls_secret_name", "es-http-tls")
    bundle = elastic_generate_http_bundle(
        ["localhost", service, prefix, "*.svc", "*.svc.cluster.local", env_param("ingress_host", "es.example.com")],
        common_name=service,
        days=365,
    )
    create_or_apply_secret(namespace, secret_name, bundle)
    apply_env_template("resources/elasticsearch/rotate-http-certs/resource/configmap.yaml", namespace=namespace)
    apply_env_template("resources/elasticsearch/rotate-http-certs/resource/statefulset.yaml", namespace=namespace)
    elastic_wait_statefulset(namespace, prefix, 3, timeout_sec=1200)
    elastic_wait_health(
        namespace,
        service,
        expected_nodes=3,
        https=True,
        auth=("elastic", env_param("elastic_password", "elasticpass")),
        timeout_sec=1200,
    )
    ingress_host = env_param("ingress_host", "es.example.com")
    ingress_class = env_param("ingress_class_name", "nginx")
    apply_yaml(
        f"""apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: es-http
  annotations:
    nginx.ingress.kubernetes.io/backend-protocol: HTTPS
spec:
  ingressClassName: {ingress_class}
  tls:
  - hosts:
    - {ingress_host}
    secretName: {secret_name}
  rules:
  - host: {ingress_host}
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: {service}
            port:
              number: 9200
""",
        namespace=namespace,
    )
    wait_until(
        lambda: elastic_curl(
            namespace,
            elastic_curl_pod(),
            f"{env_param('ingress_service_name', 'ingress-nginx-controller')}.{ingress_ns}.svc",
            "/_cluster/health?wait_for_status=yellow&timeout=5s",
            port=443,
            https=True,
            auth=("elastic", env_param("elastic_password", "elasticpass")),
            host_header=ingress_host,
        )
        .strip()
        .startswith("{"),
        timeout_sec=300,
        interval_sec=5,
        err="ingress endpoint did not become reachable",
    )


def solve_elasticsearch_seed_hosts_repair() -> None:
    namespace = elastic_namespace()
    prefix = elastic_cluster_prefix()
    service = elastic_http_service()
    apply_env_template("resources/elasticsearch/seed-hosts-repair/resource/configmap.yaml", namespace=namespace)
    run(["kubectl", "-n", namespace, "delete", "pod", f"{prefix}-2", "--ignore-not-found=true", "--wait=false"])
    elastic_wait_statefulset(namespace, prefix, 3, timeout_sec=1200)
    elastic_wait_health(namespace, service, expected_nodes=3, timeout_sec=1200)


def solve_elasticsearch_snapshot_repo_setup() -> None:
    namespace = elastic_namespace()
    service = elastic_http_service()
    pod = elastic_curl_pod()
    secret = decode_secret_data(namespace, "es-secure-settings")
    access_key = (
        secret.get("s3.client.default.access_key")
        or secret.get("access_key")
        or b""
    ).decode("utf-8")
    secret_key = (
        secret.get("s3.client.default.secret_key")
        or secret.get("secret_key")
        or b""
    ).decode("utf-8")
    if not access_key or not secret_key:
        raise RuntimeError("es-secure-settings is missing S3 access credentials")
    pods_payload = json.loads(
        capture(
            [
                "kubectl",
                "-n",
                namespace,
                "get",
                "pods",
                "-l",
                f"app={elastic_cluster_prefix()}",
                "-o",
                "json",
            ]
        )
    )
    for item in pods_payload.get("items", []):
        name = item["metadata"]["name"]
        for key, value in {
            "s3.client.default.access_key": access_key,
            "s3.client.default.secret_key": secret_key,
        }.items():
            escaped = shlex.quote(value)
            run(
                [
                    "kubectl",
                    "-n",
                    namespace,
                    "exec",
                    name,
                    "--",
                    "/bin/sh",
                    "-c",
                    f"/usr/share/elasticsearch/bin/elasticsearch-keystore remove {shlex.quote(key)} >/dev/null 2>&1 || true; printf %s {escaped} | /usr/share/elasticsearch/bin/elasticsearch-keystore add -x -f {shlex.quote(key)}",
                ]
            )
    elastic_json(
        namespace,
        pod,
        service,
        "/_nodes/reload_secure_settings",
        method="POST",
        data="{}",
        headers={"Content-Type": "application/json"},
    )
    elastic_wait_health(namespace, service, expected_nodes=3, timeout_sec=1200)
    repo_name = env_param("snapshot_repo_name", "minio-repo")
    elastic_json(
        namespace,
        pod,
        service,
        f"/_snapshot/{repo_name}",
        method="PUT",
        data='{"type":"s3","settings":{"bucket":"es-backups","endpoint":"minio:9000","protocol":"http","path_style_access":"true"}}',
        headers={"Content-Type": "application/json"},
    )
    elastic_json(
        namespace,
        pod,
        service,
        f"/_snapshot/{repo_name}/smoke-snapshot?wait_for_completion=true",
        method="PUT",
        headers={"Content-Type": "application/json"},
    )


def solve_elasticsearch_stack_monitoring_sidecars() -> None:
    namespace = elastic_namespace()
    monitoring_ns = elastic_monitoring_namespace()
    replace_json(namespace, "configmap/metricbeat-config", {"data": {"metricbeat.yml": decode_configmap_text(namespace, "metricbeat-config", "metricbeat.yml").replace("monitoring-es-htp", "monitoring-es-http")}})
    replace_json(namespace, "configmap/filebeat-config", {"data": {"filebeat.yml": decode_configmap_text(namespace, "filebeat-config", "filebeat.yml").replace("monitoring-es-htp", "monitoring-es-http")}})
    prefix = elastic_cluster_prefix()
    elastic_delete_pods(namespace, f"app={prefix}")
    elastic_wait_statefulset(namespace, prefix, 3, timeout_sec=1200)
    monitoring_pod = env_param("monitoring_curl_pod_name", "monitoring-curl-test")
    monitoring_service = env_param("monitoring_service_name", "monitoring-es-http")

    def monitoring_docs_ready() -> bool:
        try:
            indices = elastic_json(
                monitoring_ns,
                monitoring_pod,
                monitoring_service,
                "/_cat/indices?format=json",
            )
        except Exception:
            return False
        if not isinstance(indices, list):
            return False
        for index in indices:
            name = str(index.get("index") or "")
            if not name.startswith(".monitoring-es"):
                continue
            try:
                count = elastic_json(
                    monitoring_ns,
                    monitoring_pod,
                    monitoring_service,
                    f"/{name}/_count",
                )
            except Exception:
                continue
            if isinstance(count, dict) and int(count.get("count") or 0) > 0:
                return True
        return False

    wait_until(
        monitoring_docs_ready,
        timeout_sec=600,
        interval_sec=10,
        err="monitoring indices with documents did not appear",
    )


def solve_elasticsearch_transform_job_recovery() -> None:
    namespace = elastic_namespace()
    service = elastic_http_service()
    pod = elastic_curl_pod()
    transform_cluster = env_param("transform_cluster_prefix", "es-transform")
    transform_id = env_param("transform_id", "events-by-service")
    checkpoint_cm = env_param("checkpoint_configmap", "transform-checkpoint")
    checkpoint_before_raw = capture_maybe(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "configmap",
            checkpoint_cm,
            "-o",
            "jsonpath={.data.checkpoint_before}",
        ]
    )
    try:
        checkpoint_before = int((checkpoint_before_raw or "").strip() or "0")
    except ValueError:
        checkpoint_before = 0
    run(["kubectl", "-n", namespace, "scale", f"statefulset/{transform_cluster}", "--replicas=1"])
    elastic_wait_statefulset(namespace, transform_cluster, 1, timeout_sec=1200)
    elastic_curl(namespace, pod, service, f"/_transform/{transform_id}/_stop?force=true", method="POST")
    elastic_curl(namespace, pod, service, f"/_transform/{transform_id}/_start", method="POST")
    wait_until(
        lambda: isinstance(
            (stats := elastic_json(namespace, pod, service, f"/_transform/{transform_id}/_stats")),
            dict,
        )
        and (transform := (stats.get("transforms") or [{}])[0]).get("state") == "started"
        and int(
            (
                transform.get("checkpointing", {})
                .get("last", {})
                .get("checkpoint")
            )
            or (
                transform.get("stats", {})
                .get("checkpointing", {})
                .get("last", {})
                .get("checkpoint")
            )
            or 0
        )
        > checkpoint_before,
        timeout_sec=600,
        interval_sec=5,
        err="transform did not resume and advance checkpoint",
    )


def solve_elasticsearch_transport_additional_ca_trust() -> None:
    namespace = elastic_namespace()
    prefix = elastic_cluster_prefix()
    service = elastic_http_service()
    bundle_name = env_param("transport_bundle_configmap", "es-transport-ca-bundle")
    ca1 = decode_secret_data(namespace, env_param("ca1_secret_name", "es-transport-ca1"))
    ca2 = decode_secret_data(namespace, env_param("ca2_secret_name", "es-transport-ca2"))
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        bundle_path = td_path / "ca.crt"
        bundle_path.write_bytes(ca1["ca.crt"] + b"\n" + ca2["ca.crt"])
        create_or_apply_configmap_files(namespace, bundle_name, {"ca.crt": bundle_path})
    elastic_delete_pods(namespace, f"app={prefix}")
    elastic_wait_statefulset(namespace, prefix, 3, timeout_sec=1200)
    elastic_wait_health(
        namespace,
        service,
        expected_nodes=3,
        auth=("elastic", env_param("elastic_password", "elasticpass")),
        timeout_sec=1200,
    )


SOLVERS = {
    "noop": solve_noop,
    "elasticsearch_bootstrap_initial_master_nodes": solve_elasticsearch_bootstrap_initial_master_nodes,
    "elasticsearch_deploy_core_cluster": solve_elasticsearch_deploy_core_cluster,
    "elasticsearch_file_realm_user_roles_merge": solve_elasticsearch_file_realm_user_roles_merge,
    "elasticsearch_full_restart_upgrade_ha": solve_elasticsearch_full_restart_upgrade_ha,
    "elasticsearch_internal_http_service_drift": solve_elasticsearch_internal_http_service_drift,
    "elasticsearch_master_downscale_voting_exclusions": solve_elasticsearch_master_downscale_voting_exclusions,
    "elasticsearch_rotate_elastic_password": solve_elasticsearch_rotate_elastic_password,
    "elasticsearch_rotate_http_certs": solve_elasticsearch_rotate_http_certs,
    "elasticsearch_safe_downscale_with_shard_migration": solve_elasticsearch_safe_downscale_with_shard_migration,
    "elasticsearch_scale_up_new_nodeset": solve_elasticsearch_scale_up_new_nodeset,
    "elasticsearch_secure_http_ingress": solve_elasticsearch_secure_http_ingress,
    "elasticsearch_seed_hosts_repair": solve_elasticsearch_seed_hosts_repair,
    "elasticsearch_snapshot_repo_setup": solve_elasticsearch_snapshot_repo_setup,
    "elasticsearch_stack_monitoring_sidecars": solve_elasticsearch_stack_monitoring_sidecars,
    "elasticsearch_transform_job_recovery": solve_elasticsearch_transform_job_recovery,
    "elasticsearch_transport_additional_ca_trust": solve_elasticsearch_transport_additional_ca_trust,
    "demo_configmap_update": solve_demo_configmap_update,
    "demo_configmap_update_two_ns": solve_demo_configmap_update_two_ns,
    "spark_pi": solve_spark_pi,
    "spark_sql": solve_spark_sql,
    "spark_etl": solve_spark_etl,
    "spark_runtime_bundle": solve_spark_runtime_bundle,
    "spark_history": solve_spark_history,
    "spark_worker_scale": solve_spark_worker_scale,
    "spark_multi_tenant": solve_spark_multi_tenant,
    "ray_dashboard": solve_ray_dashboard,
    "ray_job_execution": solve_ray_job_execution,
    "ray_worker_scale": solve_ray_worker_scale,
    "ray_version_upgrade": solve_ray_version_upgrade,
    "ray_cluster_teardown": solve_ray_cluster_teardown,
    "nginx_route": solve_nginx_route,
    "nginx_https": solve_nginx_https,
    "nginx_class_routing": solve_nginx_class_routing,
    "nginx_canary": solve_nginx_canary,
    "nginx_rate_limit": solve_nginx_rate_limit,
    "nginx_otel": solve_nginx_otel,
    "cockroach_deploy": solve_cockroach_deploy,
    "cockroach_initialize": solve_cockroach_initialize,
    "cockroach_cluster_settings": solve_cockroach_cluster_settings,
    "cockroach_version_check": solve_cockroach_version_check,
    "cockroach_major_upgrade_finalize": solve_cockroach_major_upgrade_finalize,
    "cockroach_partitioned_update": solve_cockroach_partitioned_update,
    "cockroach_decommission": solve_cockroach_decommission,
    "cockroach_health_check_recovery": solve_cockroach_health_check_recovery,
    "cockroach_generate_cert": solve_cockroach_generate_cert,
    "cockroach_certificate_rotation": solve_cockroach_certificate_rotation,
    "cockroach_expose_ingress": solve_cockroach_expose_ingress,
    "cockroach_monitoring": solve_cockroach_monitoring,
    "cockroach_zone_config": solve_cockroach_zone_config,
    "rabbitmq_blue_green_migration": lambda: run_case_solver(
        "resources/rabbitmq-experiments/blue_green_migration/solver/solve.py"
    ),
    "rabbitmq_classic_queue": lambda: run_case_solver(
        "resources/rabbitmq-experiments/classic_queue/solver/solve.py"
    ),
    "rabbitmq_failover": lambda: run_case_solver(
        "resources/rabbitmq-experiments/failover/solver/solve.py"
    ),
    "rabbitmq_manual_backup_restore": lambda: run_case_solver(
        "resources/rabbitmq-experiments/manual_backup_restore/solver/solve.py"
    ),
    "rabbitmq_manual_monitoring": solve_rabbitmq_manual_monitoring,
    "rabbitmq_manual_policy_sync": lambda: run_case_solver(
        "resources/rabbitmq-experiments/manual_policy_sync/solver/solve.py"
    ),
    "rabbitmq_manual_runtime_reset": solve_rabbitmq_manual_runtime_reset,
    "rabbitmq_manual_skip_upgrade": lambda: run_case_solver(
        "resources/rabbitmq-experiments/manual_skip_upgrade/solver/solve.py"
    ),
    "rabbitmq_manual_tls_rotation": lambda: run_case_solver(
        "resources/rabbitmq-experiments/manual_tls_rotation/solver/solve.py"
    ),
    "rabbitmq_manual_user_permission": lambda: run_case_solver(
        "resources/rabbitmq-experiments/manual_user_permission/solver/solve.py"
    ),
    "mongodb_deploy": solve_mongodb_deploy,
    "mongodb_initialize": solve_mongodb_initialize,
    "mongodb_decommission": solve_mongodb_decommission,
    "mongodb_arbiters": solve_mongodb_arbiters,
    "mongodb_external_access_horizons": solve_mongodb_external_access_horizons,
    "mongodb_health_check_recovery": solve_mongodb_health_check_recovery,
    "mongodb_manual_rbac_reset": solve_mongodb_manual_rbac_reset,
    "mongodb_mongod_config_update": solve_mongodb_mongod_config_update,
    "mongodb_password_rotation": solve_mongodb_password_rotation,
    "mongodb_readiness_probe_tuning": solve_mongodb_readiness_probe_tuning,
    "mongodb_replica_scaling": solve_mongodb_replica_scaling,
    "mongodb_setup_rbac_drift_app": solve_mongodb_setup_rbac_drift_app,
    "mongodb_setup_rbac_drift_reporting": solve_mongodb_setup_rbac_drift_reporting,
    "mongodb_statefulset_customization": solve_mongodb_statefulset_customization,
    "mongodb_tls_setup": solve_mongodb_tls_setup,
    "mongodb_user_management": solve_mongodb_user_management,
    "mongodb_custom_roles": solve_mongodb_custom_roles,
    "mongodb_version_upgrade": solve_mongodb_version_upgrade,
    "mongodb_monitoring_integration": solve_mongodb_monitoring_integration,
    "mongodb_certificate_rotation": solve_mongodb_certificate_rotation,
}


def wait_for_submit_ack(expected_stage: str, *, timeout_sec: int = 1200, resignal_every_sec: int = 5) -> None:
    deadline = time.time() + timeout_sec
    next_resignal = time.time() + max(1, int(resignal_every_sec))
    while time.time() < deadline:
        ack_stage = submit_ack_stage_id()
        if ack_stage == expected_stage and not SUBMIT_FILE.exists():
            return
        if time.time() >= next_resignal:
            try:
                SUBMIT_FILE.touch()
                print("[repeat-stage-agent] re-touched submit.signal", flush=True)
            except Exception as exc:
                print(f"[repeat-stage-agent] failed to re-touch submit.signal: {exc}", flush=True)
            next_resignal = time.time() + max(1, int(resignal_every_sec))
        time.sleep(0.2)
    raise TimeoutError(f"timed out waiting for submit.ack for stage {expected_stage}")


def wait_for_submit_result(last_marker, *, expected_stage: str):
    deadline = time.time() + 1200
    while time.time() < deadline:
        if not SUBMIT_RESULT.exists():
            time.sleep(0.2)
            continue
        try:
            payload = json.loads(SUBMIT_RESULT.read_text(encoding="utf-8"))
        except Exception:
            time.sleep(0.2)
            continue
        workflow = payload.get("workflow") or {}
        marker = (
            payload.get("attempt"),
            workflow.get("stage_id"),
            workflow.get("stage_status"),
            workflow.get("continue"),
            workflow.get("final"),
            payload.get("can_retry"),
        )
        if marker == last_marker:
            time.sleep(0.2)
            continue
        if workflow.get("stage_id") != expected_stage:
            time.sleep(0.2)
            continue
        return payload, marker
    raise TimeoutError("timed out waiting for submit_result.json")


def wait_for_next_stage(previous_stage: str, *, timeout_sec: int = 1200) -> str:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            stage = active_stage_id()
        except Exception:
            time.sleep(0.2)
            continue
        if stage != previous_stage:
            return stage
        time.sleep(0.2)
    raise TimeoutError(f"timed out waiting for next stage after {previous_stage}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", required=True, choices=sorted(SOLVERS))
    args = parser.parse_args()

    solver = SOLVERS[args.solver]
    marker = None
    stage_id = active_stage_id()
    while True:
        try:
            solver()
        except BaseException:
            traceback.print_exc()
            return 1
        SUBMIT_FILE.touch()
        print("[repeat-stage-agent] submit.signal touched", flush=True)
        wait_for_submit_ack(stage_id)
        payload, marker = wait_for_submit_result(marker, expected_stage=stage_id)
        workflow = payload.get("workflow") or {}
        if payload.get("can_retry"):
            continue
        if workflow.get("final"):
            return 0
        stage_id = wait_for_next_stage(workflow.get("stage_id") or stage_id)


if __name__ == "__main__":
    raise SystemExit(main())
