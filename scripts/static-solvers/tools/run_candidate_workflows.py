#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from _shared import GENERATED_MANIFESTS_DIR, REPO_ROOT, STATIC_ROOT, WORKFLOWS_DIR, ensure_parent, now_utc_iso, read_yaml, write_json


RUNNER = STATIC_ROOT / "bin" / "run_current_workflow.sh"
REPORTS_DIR = STATIC_ROOT / "generated" / "validation"
DEFAULT_BATCH_DIR = REPORTS_DIR / "batch-runs"
DEFAULT_SUMMARY_PATH = REPORTS_DIR / "workflow_validation_summary.json"
DEFAULT_RESULTS_PATH = REPORTS_DIR / "workflow_validation_results.jsonl"
PROTECTED_NAMESPACES = {
    "default",
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "local-path-storage",
}
ENV_RUNTIME_PATTERNS = (
    "kubectl proxy",
    "address already in use",
    "failed to ensure namespace",
    "failed to delete namespace",
    "unable to connect to the server",
    "the server is currently unable to handle the request",
    "the connection to the server",
    "did you specify the right host or port",
    "i/o timeout",
    "tls handshake timeout",
    "connection refused",
    "object is being deleted",
    "being terminated",
    "unable to create new content in namespace",
    "timed out waiting for the condition",
)
WORKFLOW_DEFINITION_PATTERNS = (
    "unknown cluster setting",
    "unknown setting:",
    "inject_at_stage",
)
GIB = 1024 ** 3
MIB = 1024 ** 2


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _tail(text: Any, limit: int = 4000) -> str:
    text = _coerce_text(text)
    if len(text) <= limit:
        return text
    return text[-limit:]


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _extract_json_suffix(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            return json.loads(stripped)
        except Exception:
            pass
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        candidate = text[idx:].strip()
        if not candidate.startswith("{"):
            continue
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def _load_candidate_workflows() -> list[str]:
    manifest = json.loads((GENERATED_MANIFESTS_DIR / "candidate_workflows.json").read_text())
    return list(manifest.get("candidate_workflows") or [])


def _load_workflow_list(path: Path) -> list[str]:
    items: list[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        items.append(line)
    return items


def _parse_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _docker_mem_total_bytes() -> int | None:
    proc = subprocess.run(
        ["docker", "info", "--format", "{{.MemTotal}}"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _resource_preflight(workflow_rel: str, docker_mem_total_bytes: int | None) -> dict[str, Any] | None:
    if docker_mem_total_bytes is None:
        return None

    payload = read_yaml(_workflow_abspath(workflow_rel))
    stages = ((payload.get("spec") or {}).get("stages") or [])

    scale_risk_stages: list[dict[str, Any]] = []
    max_expected_nodes = 0
    for stage in stages:
        if (stage.get("service") or "") != "elasticsearch":
            continue
        if (stage.get("case") or "") != "scale-up-new-nodeset":
            continue
        params = stage.get("param_overrides") or {}
        expected_nodes = _parse_int(params.get("expected_nodes"), 5)
        original_replicas = _parse_int(params.get("original_replicas"), 3)
        max_expected_nodes = max(max_expected_nodes, expected_nodes)
        scale_risk_stages.append(
            {
                "stage_id": str(stage.get("id") or ""),
                "expected_nodes": expected_nodes,
                "original_replicas": original_replicas,
            }
        )

    if not scale_risk_stages:
        return None

    # kind reports allocatable memory per node, but Docker Desktop / Colima
    # often backs all node containers with one shared host memory pool.
    # The secure Elasticsearch scale-up path requests 1Gi per pod, so once a
    # workflow scales to 4+ nodes it can exceed the shared Docker pool even
    # though Kubernetes thinks there is per-node headroom.
    estimated_required_bytes = (max_expected_nodes * GIB) + GIB
    if max_expected_nodes >= 4 and estimated_required_bytes > docker_mem_total_bytes:
        docker_mem_gib = docker_mem_total_bytes / GIB
        estimated_required_gib = estimated_required_bytes / GIB
        return {
            "classification": "resource_issue",
            "reason": (
                "workflow includes elasticsearch/scale-up-new-nodeset to "
                f"{max_expected_nodes} nodes; estimated minimum demand "
                f"~{estimated_required_gib:.1f}Gi exceeds Docker engine total "
                f"{docker_mem_gib:.1f}Gi"
            ),
            "details": {
                "docker_mem_total_bytes": docker_mem_total_bytes,
                "docker_mem_total_gib": round(docker_mem_gib, 3),
                "max_expected_nodes": max_expected_nodes,
                "estimated_required_bytes": estimated_required_bytes,
                "estimated_required_gib": round(estimated_required_gib, 3),
                "risk_stages": scale_risk_stages,
                "heuristic": "elasticsearch_scale_up_shared_docker_memory",
            },
        }

    return None


def _snapshot_service_chain_preflight(workflow_rel: str) -> dict[str, Any] | None:
    payload = read_yaml(_workflow_abspath(workflow_rel))
    stages = ((payload.get("spec") or {}).get("stages") or [])

    seen_prior_elasticsearch_stage = False
    seen_snapshot_stage = False

    for stage in stages:
        if (stage.get("service") or "") != "elasticsearch":
            continue

        case_name = str(stage.get("case") or "")
        stage_id = str(stage.get("id") or "")

        if case_name == "snapshot-repo-setup":
            if not seen_snapshot_stage and seen_prior_elasticsearch_stage:
                return {
                    "classification": "env_chain_conflict",
                    "reason": (
                        "first elasticsearch/snapshot-repo-setup stage inherits a live "
                        "Elasticsearch namespace; the additive s3_keystore_fixture "
                        "creates MinIO + minio-init but not Service/minio, so the "
                        "workflow needs a workflow-side minio-service helper before "
                        "stage execution"
                    ),
                    "details": {
                        "stage_id": stage_id,
                        "helper": "precreate_minio_service",
                        "heuristic": "elasticsearch_snapshot_first_inherited_stage",
                    },
                }
            seen_snapshot_stage = True
            continue

        seen_prior_elasticsearch_stage = True

    return None


def _rabbitmq_permission_chain_preflight(workflow_rel: str) -> dict[str, Any] | None:
    payload = read_yaml(_workflow_abspath(workflow_rel))
    stages = ((payload.get("spec") or {}).get("stages") or [])

    seen_classic_queue = False
    seen_prior_rabbitmq_stage = False
    tls_baseline_active = False

    for stage in stages:
        if (stage.get("service") or "") != "rabbitmq":
            continue

        case_name = str(stage.get("case") or "")
        stage_id = str(stage.get("id") or "")

        if case_name == "manual_tls_rotation":
            if seen_prior_rabbitmq_stage and not tls_baseline_active:
                return {
                    "classification": "env_chain_conflict",
                    "reason": (
                        "rabbitmq/manual_tls_rotation inherits a non-TLS RabbitMQ StatefulSet and "
                        "rabbitmq-config from an earlier rabbitmq stage; its preconditions skip "
                        "replacing those live resources, so the workflow never enables the TLS "
                        "listener on 5671 for the rotation oracle. Forcing the TLS StatefulSet / "
                        "ConfigMap into the inherited namespace would also invalidate earlier "
                        "plain-AMQP stage expectations."
                    ),
                    "details": {
                        "stage_id": stage_id,
                        "heuristic": "rabbitmq_plain_stage_then_manual_tls_rotation",
                    },
                }
            tls_baseline_active = True
            seen_prior_rabbitmq_stage = True
            continue

        if case_name == "classic_queue":
            seen_classic_queue = True
            seen_prior_rabbitmq_stage = True
            continue

        if case_name == "manual_user_permission" and seen_classic_queue:
            # The active manual_user_permission solver now repairs inherited
            # app-queue declaration drift, so these workflows must execute.
            seen_prior_rabbitmq_stage = True
            continue

        seen_prior_rabbitmq_stage = True

    return None


def _load_existing_results(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        records[str(record["workflow"])] = record
    return records


def _append_result(path: Path, record: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a") as fh:
        fh.write(json.dumps(_json_safe(record), sort_keys=False) + "\n")


def _kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", *args],
        text=True,
        capture_output=True,
        check=check,
    )


def _list_namespaces() -> set[str]:
    proc = _kubectl("get", "ns", "-o", "name", "--request-timeout=5s", check=False)
    names = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        names.add(line.split("/", 1)[-1])
    return names


def _cleanup_extra_namespaces(baseline: set[str]) -> list[str]:
    current = _list_namespaces()
    extra = sorted(current - baseline - PROTECTED_NAMESPACES)
    for ns in extra:
        _kubectl("delete", "namespace", ns, "--ignore-not-found=true", "--wait=false", check=False)
    if extra:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            remaining = sorted(_list_namespaces() - baseline - PROTECTED_NAMESPACES)
            if not remaining:
                break
            time.sleep(2)
    return extra


def _workflow_abspath(workflow_rel: str) -> Path:
    return WORKFLOWS_DIR / workflow_rel


def _runs_root(batch_dir: Path) -> Path:
    return batch_dir / "runs"


def _next_run_dir(runs_root: Path, before: set[str]) -> Path | None:
    after = {p.name for p in runs_root.iterdir() if p.is_dir()} if runs_root.exists() else set()
    new_dirs = sorted(after - before)
    if new_dirs:
        return runs_root / new_dirs[-1]
    if not runs_root.exists():
        return None
    dirs = sorted((p for p in runs_root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    return dirs[-1] if dirs else None


def _run_workflow(workflow_file: Path, batch_dir: Path, *, timeout_sec: int = 1800) -> dict[str, Any]:
    runs_root = _runs_root(batch_dir)
    runs_root.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in runs_root.iterdir() if p.is_dir()}
    env = os.environ.copy()
    env["PATH"] = f"{STATIC_ROOT / 'bin'}:{env.get('PATH', '')}"
    python_bin = sys.executable or "python3"
    cmd = [
        python_bin,
        "orchestrator.py",
        "run-workflow",
        str(workflow_file),
        "--sandbox",
        "local",
        "--runs-dir",
        str(runs_root),
        "--output",
        "json",
        "--agent-cmd",
        f"bash {RUNNER} {workflow_file}",
    ]
    started_at = time.monotonic()
    timed_out = False
    stdout = ""
    stderr = ""
    exit_code = 0
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            env=env,
        )
        stdout = proc.stdout
        stderr = proc.stderr
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = _coerce_text(exc.stdout)
        stderr = _coerce_text(exc.stderr)
        exit_code = 124
    run_dir = _next_run_dir(runs_root, before)
    run_json = None
    if run_dir is not None:
        run_json_path = run_dir / "run.json"
        if run_json_path.exists():
            try:
                run_json = json.loads(run_json_path.read_text())
            except Exception:
                run_json = None
    if run_json is None:
        run_json = _extract_json_suffix(stdout)
    return {
        "exit_code": exit_code,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
        "duration_sec": time.monotonic() - started_at,
        "run_dir": str(run_dir) if run_dir is not None else "",
        "run_json": run_json,
        "timed_out": timed_out,
    }


def _first_failed_stage(run_json: dict[str, Any] | None) -> dict[str, Any] | None:
    for stage in (run_json or {}).get("stages") or []:
        if stage.get("status") != "pass":
            return stage
    return None


def _has_regression_failures(run_json: dict[str, Any] | None) -> list[str]:
    failures: list[str] = []
    sweep = (run_json or {}).get("regression_sweep") or {}
    if isinstance(sweep, dict):
        for stage_id, payload in sweep.items():
            verdict = (payload or {}).get("verdict")
            if verdict and verdict != "pass":
                failures.append(stage_id)
    return failures


def _failed_stage_context(run_record: dict[str, Any], failed_stage: dict[str, Any] | None) -> str:
    chunks = [
        str(run_record.get("stdout_tail") or ""),
        str(run_record.get("stderr_tail") or ""),
    ]
    if not failed_stage:
        return "\n".join(chunk for chunk in chunks if chunk)

    error_text = str(failed_stage.get("error") or "")
    if error_text:
        chunks.append(error_text)

    for path_key in ("oracle_path", "evidence_path"):
        raw_path = str(failed_stage.get(path_key) or "")
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.exists():
            continue
        try:
            chunks.append(path.read_text())
        except Exception:
            pass

    run_dir = Path(str(run_record.get("run_dir") or ""))
    stage_id = str(failed_stage.get("stage_id") or "")
    if run_dir and stage_id:
        for name in ("precondition.log", "agent.log", "oracle.json"):
            path = run_dir / "stages" / stage_id / name
            if not path.exists():
                continue
            try:
                chunks.append(path.read_text())
            except Exception:
                pass

    return "\n".join(chunk for chunk in chunks if chunk)


def _contains_env_runtime_signature(*texts: str) -> bool:
    blob = "\n".join(texts).lower()
    return any(pattern in blob for pattern in ENV_RUNTIME_PATTERNS)


def _contains_workflow_definition_signature(*texts: str) -> bool:
    blob = "\n".join(texts).lower()
    return any(pattern in blob for pattern in WORKFLOW_DEFINITION_PATTERNS)


def _extract_matching_line(patterns: tuple[str, ...], *texts: str) -> str:
    lowered_patterns = tuple(pattern.lower() for pattern in patterns)
    for text in texts:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            lowered = line.lower()
            if any(pattern in lowered for pattern in lowered_patterns):
                return line
    return ""


def _workflow_stage_map(workflow_rel: str) -> dict[str, dict[str, Any]]:
    payload = read_yaml(_workflow_abspath(workflow_rel))
    stages = ((payload.get("spec") or {}).get("stages") or [])
    return {str(stage.get("id") or ""): stage for stage in stages}


def _write_single_stage_probe(workflow_rel: str, stage_id: str) -> Path:
    payload = read_yaml(_workflow_abspath(workflow_rel))
    spec = payload.get("spec") or {}
    stage = _workflow_stage_map(workflow_rel)[stage_id]
    probe_dir = REPORTS_DIR / "probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    safe_name = workflow_rel.replace("/", "__").replace(".yaml", "")
    probe_path = probe_dir / f"{safe_name}__{stage_id}.yaml"
    probe_payload = {
        "metadata": {
            "id": f"probe-{safe_name}-{stage_id}",
            "label": f"Probe {workflow_rel} {stage_id}",
            "source_workflow": workflow_rel,
            "source_stage_id": stage_id,
        },
        "spec": {
            "prompt_mode": spec.get("prompt_mode") or "progressive",
            "stages": [stage],
        },
    }
    probe_path.write_text(json.dumps(probe_payload, indent=2) + "\n")
    return probe_path


def _probe_artifact_context(run_dir: Path) -> str:
    """Collect probe-stage artifacts even when the probe times out pre-agent."""
    chunks: list[str] = []
    if not run_dir.exists():
        return ""
    for path in sorted(run_dir.glob("stages/*/precondition.log")):
        try:
            chunks.append(path.read_text())
        except Exception:
            pass
    for path in sorted(run_dir.glob("stages/*/agent.log")):
        try:
            chunks.append(path.read_text())
        except Exception:
            pass
    for path in sorted(run_dir.glob("stages/*/oracle.json")):
        try:
            chunks.append(path.read_text())
        except Exception:
            pass
    return "\n".join(chunk for chunk in chunks if chunk)


def _classify_probe(run_record: dict[str, Any]) -> tuple[str, str]:
    run_json = run_record.get("run_json")
    failed_stage = _first_failed_stage(run_json)
    context = _failed_stage_context(run_record, failed_stage)
    if run_json and run_json.get("status") == "complete" and not _has_regression_failures(run_json):
        return "pass", "standalone stage passed"
    if _contains_env_runtime_signature(context):
        detail = _extract_matching_line(ENV_RUNTIME_PATTERNS, context)
        return "env_runtime_issue", detail or "runtime issue"
    if run_record.get("timed_out"):
        if _contains_workflow_definition_signature(context):
            detail = _extract_matching_line(WORKFLOW_DEFINITION_PATTERNS, context)
            return "workflow_definition_issue", detail or "probe timed out on unsupported workflow definition"
        return "solver_issue", "standalone probe timed out"
    if run_json and run_json.get("status") == "error" and not ((run_json.get("stages") or [])):
        top_error = str(run_json.get("error") or run_record.get("stdout_tail") or "probe workflow-level error")
        if _contains_env_runtime_signature(top_error):
            return "env_runtime_issue", top_error
        if _contains_workflow_definition_signature(top_error):
            detail = _extract_matching_line(WORKFLOW_DEFINITION_PATTERNS, top_error)
            return "workflow_definition_issue", detail or top_error
        return "solver_issue", top_error
    if _contains_workflow_definition_signature(context):
        detail = _extract_matching_line(WORKFLOW_DEFINITION_PATTERNS, context)
        return "workflow_definition_issue", detail or "unsupported workflow definition"
    if failed_stage and _contains_env_runtime_signature(
        str(failed_stage.get("error") or ""),
        str(run_record.get("stdout_tail") or ""),
        str(run_record.get("stderr_tail") or ""),
    ):
        return "env_runtime_issue", str(failed_stage.get("error") or "runtime issue")
    details = str((failed_stage or {}).get("error") or "").strip()
    if not details:
        details = str(run_record.get("stdout_tail") or "").strip() or str(run_record.get("stderr_tail") or "").strip()
    return "solver_issue", details or "standalone stage did not pass"


def _retry_timed_out_probe(
    workflow_rel: str,
    stage_id: str,
    batch_dir: Path,
    probe_run: dict[str, Any],
    probe_class: str,
    probe_reason: str,
) -> tuple[str, str, dict[str, Any]]:
    """Retry 180s standalone-probe timeouts at a longer window before classifying."""
    if probe_class != "solver_issue":
        return probe_class, probe_reason, probe_run
    if probe_reason != "standalone probe timed out":
        return probe_class, probe_reason, probe_run
    if not probe_run.get("timed_out"):
        return probe_class, probe_reason, probe_run

    probe_path = _write_single_stage_probe(workflow_rel, stage_id)
    long_probe_batch_dir = batch_dir / "probe-batch-long"
    long_probe = probe_run
    long_class = probe_class
    long_reason = probe_reason

    for _ in range(2):
        baseline_namespaces = _list_namespaces()
        long_probe = _run_workflow(probe_path, long_probe_batch_dir, timeout_sec=600)
        long_class, long_reason = _classify_probe(long_probe)
        _cleanup_extra_namespaces(baseline_namespaces)
        if long_class == "pass":
            return (
                "env_chain_conflict",
                "failed in chained workflow but standalone stage passed on longer rerun beyond the 180s probe timeout",
                long_probe,
            )
        if long_class in {"env_runtime_issue", "workflow_definition_issue"}:
            return long_class, long_reason, long_probe

    if long_class == "solver_issue":
        probe_context = _probe_artifact_context(Path(str(long_probe.get("run_dir") or "")))
        if _contains_env_runtime_signature(
            str(long_probe.get("stdout_tail") or ""),
            str(long_probe.get("stderr_tail") or ""),
            probe_context,
        ):
            detail = _extract_matching_line(
                ENV_RUNTIME_PATTERNS,
                str(long_probe.get("stdout_tail") or ""),
                str(long_probe.get("stderr_tail") or ""),
                probe_context,
            )
            return "env_runtime_issue", detail or "runtime issue", long_probe
        if _contains_workflow_definition_signature(
            str(long_probe.get("stdout_tail") or ""),
            str(long_probe.get("stderr_tail") or ""),
            probe_context,
        ):
            detail = _extract_matching_line(
                WORKFLOW_DEFINITION_PATTERNS,
                str(long_probe.get("stdout_tail") or ""),
                str(long_probe.get("stderr_tail") or ""),
                probe_context,
            )
            return "workflow_definition_issue", detail or "workflow definition issue", long_probe

    return long_class, long_reason, long_probe


def _classify_candidate_failure(
    workflow_rel: str,
    run_record: dict[str, Any],
    batch_dir: Path,
    baseline_namespaces: set[str],
) -> tuple[str, str, dict[str, Any] | None]:
    run_json = run_record.get("run_json")
    failed_stage = _first_failed_stage(run_json)
    if failed_stage and _contains_env_runtime_signature(
        str(failed_stage.get("error") or ""),
        str(run_record.get("stdout_tail") or ""),
        str(run_record.get("stderr_tail") or ""),
    ):
        return "env_runtime_issue", str(failed_stage.get("error") or "runtime issue"), None
    if failed_stage is None:
        return "env_runtime_issue", "run failed without a stage result", None
    stage_context = _failed_stage_context(run_record, failed_stage)
    if _contains_workflow_definition_signature(stage_context):
        detail = _extract_matching_line(WORKFLOW_DEFINITION_PATTERNS, stage_context)
        return "workflow_definition_issue", detail or "unsupported workflow definition", None
    if _contains_env_runtime_signature(stage_context):
        detail = _extract_matching_line(ENV_RUNTIME_PATTERNS, stage_context)
        return "env_runtime_issue", detail or "runtime issue", None

    _cleanup_extra_namespaces(baseline_namespaces)
    probe_path = _write_single_stage_probe(workflow_rel, str(failed_stage.get("stage_id") or ""))
    probe_batch_dir = batch_dir / "probe-batch"
    probe_run = _run_workflow(probe_path, probe_batch_dir, timeout_sec=180)
    probe_class, probe_reason = _classify_probe(probe_run)
    probe_class, probe_reason, probe_run = _retry_timed_out_probe(
        workflow_rel,
        str(failed_stage.get("stage_id") or ""),
        batch_dir,
        probe_run,
        probe_class,
        probe_reason,
    )
    if probe_class == "pass":
        return "env_chain_conflict", "failed in chained workflow but standalone stage passed", probe_run
    if probe_class == "env_chain_conflict":
        return "env_chain_conflict", probe_reason, probe_run
    if probe_class == "env_runtime_issue":
        return "env_runtime_issue", probe_reason, probe_run
    if probe_class == "workflow_definition_issue":
        return "workflow_definition_issue", probe_reason, probe_run
    return "solver_issue", probe_reason, probe_run


def _classify_candidate_result(
    workflow_rel: str,
    run_record: dict[str, Any],
    batch_dir: Path,
    baseline_namespaces: set[str],
) -> tuple[str, str, dict[str, Any] | None]:
    run_json = run_record.get("run_json")
    if run_json and run_json.get("status") == "error" and not ((run_json.get("stages") or [])):
        top_error = str(run_json.get("error") or run_record.get("stdout_tail") or "workflow-level error")
        if _contains_env_runtime_signature(top_error):
            return "env_runtime_issue", top_error, None
        return "workflow_definition_issue", top_error, None
    if run_json and run_json.get("status") == "complete":
        regression_failures = _has_regression_failures(run_json)
        if regression_failures:
            return "pass", f"workflow completed; regression sweep failed for {', '.join(regression_failures)}", None
        return "pass", "workflow completed", None
    return _classify_candidate_failure(workflow_rel, run_record, batch_dir, baseline_namespaces)


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(record["classification"] for record in records)
    return {
        "generated_at": now_utc_iso(),
        "total_records": len(records),
        "counts": dict(sorted(counts.items())),
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run static solver workflows and classify failures.")
    parser.add_argument("--limit", type=int, default=0, help="Only run the first N candidate workflows (0 = all).")
    parser.add_argument("--batch-dir", default=str(DEFAULT_BATCH_DIR))
    parser.add_argument("--results-path", default=str(DEFAULT_RESULTS_PATH))
    parser.add_argument("--summary-path", default=str(DEFAULT_SUMMARY_PATH))
    parser.add_argument("--workflow-list", default="", help="Optional newline-delimited workflow list to run instead of the default candidate manifest.")
    parser.add_argument("--resume", action="store_true", help="Skip workflows already present in the results file.")
    args = parser.parse_args()

    batch_dir = Path(args.batch_dir).resolve()
    results_path = Path(args.results_path).resolve()
    summary_path = Path(args.summary_path).resolve()
    batch_dir.mkdir(parents=True, exist_ok=True)
    ensure_parent(results_path)
    ensure_parent(summary_path)

    if args.workflow_list:
        candidate_workflows = _load_workflow_list(Path(args.workflow_list).resolve())
    else:
        candidate_workflows = _load_candidate_workflows()
    if args.limit > 0:
        candidate_workflows = candidate_workflows[: args.limit]

    existing = _load_existing_results(results_path) if args.resume else {}
    baseline_namespaces = _list_namespaces()
    completed_records = list(existing.values())
    docker_mem_total_bytes = _docker_mem_total_bytes()

    for index, workflow_rel in enumerate(candidate_workflows, start=1):
        if workflow_rel in existing:
            print(f"[{index}/{len(candidate_workflows)}] skip existing {workflow_rel}", flush=True)
            continue

        preflight = _resource_preflight(workflow_rel, docker_mem_total_bytes)
        if preflight is None:
            preflight = _snapshot_service_chain_preflight(workflow_rel)
        if preflight is None:
            preflight = _rabbitmq_permission_chain_preflight(workflow_rel)
        if preflight is not None:
            record = {
                "workflow": workflow_rel,
                "executed_at": now_utc_iso(),
                "classification": preflight["classification"],
                "reason": preflight["reason"],
                "run_dir": "",
                "run_id": "",
                "run_status": "skipped_preflight",
                "duration_sec": 0.0,
                "exit_code": None,
                "failed_stage": None,
                "regression_failures": [],
                "cleaned_namespaces": [],
                "stdout_tail": "",
                "stderr_tail": "",
                "preflight_audit": preflight["details"],
            }
            _append_result(results_path, record)
            existing[workflow_rel] = record
            completed_records.append(record)
            write_json(summary_path, _summarize(completed_records))
            print(
                f"[{index}/{len(candidate_workflows)}] {workflow_rel} -> {record['classification']}"
                + (f" ({record['reason']})" if record["reason"] else ""),
                flush=True,
            )
            continue

        print(f"[{index}/{len(candidate_workflows)}] run {workflow_rel}", flush=True)
        workflow_file = _workflow_abspath(workflow_rel)
        run_record = _run_workflow(workflow_file, batch_dir)
        classification, reason, probe_run = _classify_candidate_result(
            workflow_rel,
            run_record,
            batch_dir,
            baseline_namespaces,
        )
        cleaned = _cleanup_extra_namespaces(baseline_namespaces)

        record = {
            "workflow": workflow_rel,
            "executed_at": now_utc_iso(),
            "classification": classification,
            "reason": reason,
            "run_dir": run_record.get("run_dir") or "",
            "run_id": ((run_record.get("run_json") or {}).get("run_id") or ""),
            "run_status": ((run_record.get("run_json") or {}).get("status") or ""),
            "duration_sec": run_record.get("duration_sec"),
            "exit_code": run_record.get("exit_code"),
            "failed_stage": _first_failed_stage(run_record.get("run_json")),
            "regression_failures": _has_regression_failures(run_record.get("run_json")),
            "cleaned_namespaces": cleaned,
            "stdout_tail": run_record.get("stdout_tail") or "",
            "stderr_tail": run_record.get("stderr_tail") or "",
        }
        if probe_run is not None:
            record["probe"] = {
                "run_dir": probe_run.get("run_dir") or "",
                "run_id": ((probe_run.get("run_json") or {}).get("run_id") or ""),
                "run_status": ((probe_run.get("run_json") or {}).get("status") or ""),
                "exit_code": probe_run.get("exit_code"),
                "stdout_tail": probe_run.get("stdout_tail") or "",
                "stderr_tail": probe_run.get("stderr_tail") or "",
                "failed_stage": _first_failed_stage(probe_run.get("run_json")),
                "regression_failures": _has_regression_failures(probe_run.get("run_json")),
            }

        _append_result(results_path, record)
        existing[workflow_rel] = record
        completed_records.append(record)
        write_json(summary_path, _summarize(completed_records))
        print(
            f"[{index}/{len(candidate_workflows)}] {workflow_rel} -> {classification}"
            + (f" ({reason})" if reason else ""),
            flush=True,
        )

    write_json(summary_path, _summarize(list(existing.values())))
    print(json.dumps(_summarize(list(existing.values())), indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
