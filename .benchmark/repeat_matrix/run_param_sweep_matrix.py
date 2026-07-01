#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml


SMOKE_DIR = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
RESOURCES = ROOT / "resources"
WORKFLOW_DIR = SMOKE_DIR / "generated"
PROFILE = SMOKE_DIR / "local_workflow_profile.yaml"
AGENT = SMOKE_DIR / "repeat_stage_agent.py"
SUMMARY = SMOKE_DIR / "param_sweep_matrix_summary.json"

TIMEOUT_PROFILES: dict[str, dict[str, int] | None] = {
    "declared": None,
    "fixed_fast_fail": {
        "setup": 240,
        "verify": 180,
        "cleanup": 180,
    },
}


def _load_repeat_matrix_module():
    path = SMOKE_DIR / "run_repeat_matrix.py"
    spec = importlib.util.spec_from_file_location("repeat_matrix_module", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load repeat matrix module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPEAT_MATRIX = _load_repeat_matrix_module()


VALUE_OVERRIDES = {
    "target_value": "sweep-value-1",
    "source_value": "left-sweep",
    "expected_output": "pong-ps",
}

SUFFIX_VALUE_KEYS = {
    "expected_summary",
}

CLUSTER_PREFIX_RE = re.compile(r"(?:^|_)(?:cluster|blue_cluster|green_cluster|data_cluster|arbiter_cluster|prod_cluster|dev_cluster|transform_cluster)_prefix$")
SERVICE_ACCOUNT_PREFIX_RE = re.compile(r"(?:^|_)service_account_prefix$")
JOB_PREFIX_RE = re.compile(r"(?:^|_)job_name_prefix$")
HOST_RE = re.compile(r"(?:^|_)(?:host|ui_host|ingress_host|external_host_prefix)$")
K8S_NAME_RE = re.compile(
    r"(?:^|_)(?:service|headless_service|http_service|ingress|deployment|monitoring_deployment|ingress_controller_deployment|"
    r"service_account|job|configmap|secret|report_configmap|service_monitor|pvc|openssl_pod|curl_pod|producer)_name$"
)
LOGICAL_NAME_RE = re.compile(
    r"(?:^|_)(?:username|user|role_name|database|collection|index_name|queue|vhost|report_key|setting_name)$"
)


def _parse_payload(stdout: str):
    marker = "[\n  {"
    pos = stdout.find(marker)
    if pos < 0:
        marker = "[{"
        pos = stdout.find(marker)
    if pos < 0:
        raise RuntimeError(stdout)
    return json.loads(stdout[pos:])


def _python_exec() -> Path:
    python_exec = getattr(REPEAT_MATRIX, "PYTHON", None)
    if isinstance(python_exec, Path) and python_exec.exists():
        return python_exec
    candidate = ROOT / ".venv" / "bin" / "python"
    return candidate if candidate.exists() else Path(sys.executable)


def _yaml_case_data(service: str, case: str) -> dict:
    path = RESOURCES / service / case / "test.yaml"
    return yaml.safe_load(path.read_text()) or {}


def _param_defs(case_data: dict) -> dict:
    defs = ((case_data.get("params") or {}).get("definitions") or {})
    return defs if isinstance(defs, dict) else {}


def _append_suffix(value: object, suffix: str = "-ps") -> str:
    text = str(value).strip()
    if not text:
        return text
    if text.endswith(suffix):
        return text
    return f"{text}{suffix}"


def _suffix_host(value: object) -> str:
    text = str(value).strip()
    if not text:
        return text
    if "." not in text:
        return _append_suffix(text)
    head, tail = text.split(".", 1)
    if head.endswith("-ps"):
        return text
    return f"{head}-ps.{tail}"


def _default_value(spec: dict):
    return spec.get("default")


def _string_value_override(key: str, spec: dict):
    default = _default_value(spec)
    if default is None:
        return None
    if key in VALUE_OVERRIDES:
        return VALUE_OVERRIDES[key]
    if key in SUFFIX_VALUE_KEYS:
        return _append_suffix(default)
    if CLUSTER_PREFIX_RE.search(key):
        return _append_suffix(default)
    if SERVICE_ACCOUNT_PREFIX_RE.search(key):
        return _append_suffix(default)
    if JOB_PREFIX_RE.search(key):
        return _append_suffix(default)
    if HOST_RE.search(key):
        return _suffix_host(default)
    if K8S_NAME_RE.search(key):
        return _append_suffix(default)
    if LOGICAL_NAME_RE.search(key):
        return _append_suffix(default)
    return None


def _generate_overrides(case_key: str, case_data: dict) -> tuple[dict, str]:
    defs = _param_defs(case_data)
    keys = list(defs.keys())
    overrides: dict[str, object] = {}

    for key in keys:
        spec = defs.get(key) or {}
        if CLUSTER_PREFIX_RE.search(key):
            value = _string_value_override(key, spec)
            if value is not None:
                overrides[key] = value

    if overrides:
        return overrides, "cluster_identity"

    for key in keys:
        spec = defs.get(key) or {}
        if key in VALUE_OVERRIDES:
            value = _string_value_override(key, spec)
            if value is not None:
                overrides[key] = value
    if overrides:
        return overrides, "literal_value"

    name_like_count = 0
    host_done = False
    for key in keys:
        spec = defs.get(key) or {}
        value = _string_value_override(key, spec)
        if value is None:
            continue
        if HOST_RE.search(key):
            if host_done:
                continue
            overrides[key] = value
            host_done = True
            continue
        if K8S_NAME_RE.search(key) or SERVICE_ACCOUNT_PREFIX_RE.search(key) or JOB_PREFIX_RE.search(key):
            overrides[key] = value
            name_like_count += 1
            if name_like_count >= 3:
                break
    if overrides:
        return overrides, "resource_identity"

    for key in keys:
        spec = defs.get(key) or {}
        value = _string_value_override(key, spec)
        if value is not None:
            overrides[key] = value
            return overrides, "logical_identity"

    return {}, "no_override"


def _workflow_path(case_entry: dict) -> Path:
    slug = f"{case_entry['service']}-{case_entry['case']}".replace("/", "-")
    return WORKFLOW_DIR / f"param_sweep_{slug}.json"


def _run_glob(case_entry: dict) -> str:
    slug = f"{case_entry['service']}-{case_entry['case']}".replace("/", "-")
    return f"*workflow_run_param-sweep-{slug}"


def _launcher_env_and_args() -> tuple[dict[str, str], list[str]]:
    env = os.environ.copy()
    extra_args: list[str] = []
    source_kubeconfig = (env.get("KUBECONFIG") or "").strip()
    proxy_server = (env.get("BENCHMARK_PROXY_SERVER") or "").strip()
    if not proxy_server and str((yaml.safe_load(PROFILE.read_text(encoding="utf-8")) or {}).get("sandbox") or "").strip().lower() == "local":
        try:
            raw = subprocess.check_output(
                ["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"],
                text=True,
                env={**env, **({"KUBECONFIG": source_kubeconfig} if source_kubeconfig else {})},
            ).strip()
        except Exception:
            raw = ""
        if raw:
            proxy_server = raw.replace("https://", "").replace("http://", "").split("/", 1)[0]
    if source_kubeconfig:
        extra_args.extend(["--source-kubeconfig", source_kubeconfig])
    if proxy_server:
        extra_args.extend(["--proxy-server", proxy_server])
    return env, extra_args


def _effective_timeouts(case_entry: dict, timeout_profile: str) -> dict[str, str]:
    raw = dict(REPEAT_MATRIX._default_timeouts(case_entry))
    caps = TIMEOUT_PROFILES[timeout_profile]
    out: dict[str, str] = {}
    for key, value in raw.items():
        try:
            num = int(value)
        except (TypeError, ValueError):
            out[key] = str(value)
            continue
        cap = None if caps is None else caps.get(key)
        out[key] = str(min(num, cap) if cap is not None else num)
    return out


def _write_workflow(case_entry: dict, overrides: dict) -> Path:
    path = _workflow_path(case_entry)
    workflow = {
        "apiVersion": "benchmark/v1",
        "kind": "Workflow",
        "metadata": {"name": f"param-sweep-{case_entry['service']}-{case_entry['case']}"},
        "spec": {
            "prompt_mode": "progressive",
            "stages": [
                {
                    "id": "stage_first",
                    "service": case_entry["service"],
                    "case": case_entry["case"],
                    "namespaces": case_entry["aliases"],
                    "param_overrides": overrides,
                },
                {
                    "id": "stage_second",
                    "service": case_entry["service"],
                    "case": case_entry["case"],
                    "namespaces": case_entry["aliases"],
                    "param_overrides": overrides,
                },
            ],
        },
    }
    if case_entry["aliases"]:
        workflow["spec"]["namespaces"] = list(case_entry["aliases"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")
    return path


def _read_effective_params(abs_run_dir: Path) -> dict:
    path = abs_run_dir / "effective_params.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _workflow_state(abs_run_dir: Path) -> dict:
    path = abs_run_dir / "workflow_state.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _terminal_workflow_ready(abs_run_dir: Path) -> bool:
    workflow_state = _workflow_state(abs_run_dir)
    if not workflow_state:
        return False
    if not workflow_state.get("terminal"):
        return False
    statuses = workflow_state.get("stage_statuses") or []
    return bool(statuses) and all(status is not None for status in statuses)


def _cleanup_log_present(abs_run_dir: Path) -> bool:
    return (abs_run_dir / "workflow_cleanup.log").exists()


def _stage_results(abs_run_dir: Path) -> list[dict]:
    path = abs_run_dir / "workflow_stage_results.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _summarize_run_artifacts(abs_run_dir: Path) -> dict:
    out: dict[str, object] = {"abs_run_dir": str(abs_run_dir)}
    effective = _read_effective_params(abs_run_dir)
    workflow_state = _workflow_state(abs_run_dir)
    stage_results = _stage_results(abs_run_dir)
    warning_messages = []
    if isinstance(effective, dict):
        for stage in effective.values():
            if isinstance(stage, dict):
                warning_messages.extend(stage.get("warnings") or [])
    out["stage_param_warnings"] = workflow_state.get("stage_param_warnings") or {}
    out["warning_messages"] = sorted(set(warning_messages))
    out["terminal_reason"] = workflow_state.get("terminal_reason")
    out["stage_results"] = stage_results
    failed_stage = None
    failure_reason = None
    for row in stage_results:
        if str(row.get("status") or "").strip().lower() != "passed":
            failed_stage = row.get("stage_id")
            failure_reason = row.get("reason")
            break
    out["failed_stage_id"] = failed_stage
    out["failure_reason"] = failure_reason
    preop = REPEAT_MATRIX._read_stage_two_preop(abs_run_dir)
    preop.pop("second_stage_preoperation_excerpt", None)
    out.update(preop)
    return out


def _classify(row: dict) -> str:
    if not row.get("solver"):
        return "no_smoke_solver"
    if not row.get("overrides"):
        return "no_override"
    if row.get("returncode") != 0:
        return "runner_error"
    status = str((row.get("result") or {}).get("status") or "").strip().lower()
    if status == "passed":
        return "passed"
    return "failed"


def _stdout_tail(path: Path) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-120:])


def _result_status_from_row(row: dict) -> str:
    return "passed" if row.get("terminal_reason") == "workflow_complete" and not row.get("failure_reason") else "failed"


def _run_case(case_entry: dict, timeout_profile: str) -> dict:
    case_key = case_entry["key"]
    case_data = _yaml_case_data(case_entry["service"], case_entry["case"])
    overrides, profile = _generate_overrides(case_key, case_data)
    row = {
        "key": case_key,
        "service": case_entry["service"],
        "case": case_entry["case"],
        "solver": case_entry.get("solver"),
        "aliases": list(case_entry.get("aliases") or []),
        "timeouts": dict(case_entry.get("timeouts") or {}),
        "override_profile": profile,
        "overrides": overrides,
    }
    if not case_entry.get("solver") or not overrides:
        row["classification"] = _classify(row)
        return row

    wf_path = _write_workflow(case_entry, overrides)
    timeouts = _effective_timeouts(case_entry, timeout_profile)
    row["effective_timeouts"] = dict(timeouts)
    row["timeout_profile"] = timeout_profile
    env, launcher_args = _launcher_env_and_args()
    cmd = [
        str(_python_exec()),
        "orchestrator.py",
        "workflow-run",
        "--profile",
        str(PROFILE),
        "--workflow",
        str(wf_path),
        "--agent-cmd",
        f"python3 {AGENT} --solver {case_entry['solver']}",
        "--submit-timeout",
        timeouts["submit"],
        "--setup-timeout",
        timeouts["setup"],
        "--setup-timeout-mode",
        "fixed",
        "--verify-timeout",
        timeouts["verify"],
        "--cleanup-timeout",
        timeouts["cleanup"],
        "--max-attempts",
        "1",
        "--stage-failure-mode",
        "terminate",
    ]
    cmd.extend(launcher_args)
    row["command"] = cmd
    row["workflow_path"] = str(wf_path)
    baseline = {p.resolve() for p in ROOT.glob(f"runs/{_run_glob(case_entry)}")}
    with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8", delete=False) as tmp:
        stdout_path = Path(tmp.name)
    stdout_handle = stdout_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=stdout_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    abs_run_dir = None
    deadline = time.time() + 120
    while time.time() < deadline and abs_run_dir is None and proc.poll() is None:
        candidates = sorted(
            (p.resolve() for p in ROOT.glob(f"runs/{_run_glob(case_entry)}") if p.resolve() not in baseline),
            key=lambda p: p.stat().st_mtime,
        )
        if candidates:
            abs_run_dir = candidates[-1]
            break
        time.sleep(1)
    if abs_run_dir is not None:
        terminal_deadline = time.time() + 1800
        cleanup_deadline = None
        while time.time() < terminal_deadline and proc.poll() is None:
            if _terminal_workflow_ready(abs_run_dir):
                cleanup_deadline = cleanup_deadline or (time.time() + 600)
                if _cleanup_log_present(abs_run_dir):
                    break
                if time.time() >= cleanup_deadline:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=10)
                    stdout_handle.close()
                    row.update(_summarize_run_artifacts(abs_run_dir))
                    row["result"] = {
                        "run_dir": str(abs_run_dir.relative_to(ROOT)),
                        "status": _result_status_from_row(row),
                    }
                    row["returncode"] = 0
                    row["terminated_after_terminal_state"] = True
                    row["stdout_tail"] = _stdout_tail(stdout_path)
                    row["classification"] = _classify(row)
                    try:
                        stdout_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return row
            time.sleep(2)
    proc.wait()
    stdout_handle.close()
    row["returncode"] = proc.returncode
    row["stdout_tail"] = _stdout_tail(stdout_path)
    if abs_run_dir is not None and _workflow_state(abs_run_dir):
        row.update(_summarize_run_artifacts(abs_run_dir))
        row["result"] = {
            "run_dir": str(abs_run_dir.relative_to(ROOT)),
            "status": _result_status_from_row(row),
        }
    elif proc.returncode == 0:
        payload = _parse_payload(stdout_path.read_text(encoding="utf-8", errors="replace"))
        result = (payload[0] or {}).get("result") or {}
        row["result"] = result
        run_dir = result.get("run_dir")
        if run_dir:
            abs_run_dir = ROOT / run_dir
            row.update(_summarize_run_artifacts(abs_run_dir))
    try:
        stdout_path.unlink(missing_ok=True)
    except Exception:
        pass

    row["classification"] = _classify(row)
    return row


def _selected_cases(filters: list[str]) -> list[dict]:
    all_cases = REPEAT_MATRIX.discover_active_cases()
    if not filters:
        return all_cases
    wanted = set(filters)
    out = []
    for case in all_cases:
        if case["service"] in wanted or case["case"] in wanted or case["key"] in wanted:
            out.append(case)
    return out


def _write_summary(rows: list[dict]) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        key = row.get("classification") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    payload = {
        "total_cases": len(rows),
        "classification_counts": dict(sorted(counts.items())),
        "cases": rows,
    }
    SUMMARY.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("filters", nargs="*")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--runnable-only", action="store_true")
    parser.add_argument(
        "--timeout-profile",
        choices=sorted(TIMEOUT_PROFILES),
        default="declared",
        help="How aggressively to cap testcase-declared timeouts during the sweep.",
    )
    args = parser.parse_args()

    selected = _selected_cases(args.filters)
    if args.filters and not selected:
        print(f"no cases matched filters: {args.filters}", file=sys.stderr)
        return 1
    if args.runnable_only:
        selected = [case for case in selected if case.get("solver")]

    if args.list:
        for case in selected:
            case_data = _yaml_case_data(case["service"], case["case"])
            overrides, profile = _generate_overrides(case["key"], case_data)
            print(
                json.dumps(
                    {
                        "key": case["key"],
                        "solver": case.get("solver"),
                        "profile": profile,
                        "overrides": overrides,
                    }
                )
            )
        return 0

    SUMMARY.unlink(missing_ok=True)
    rows = []
    for case in selected:
        print(f"[param-sweep-matrix] running {case['key']}", flush=True)
        row = _run_case(case, args.timeout_profile)
        rows.append(row)
        _write_summary(rows)
        print(
            f"[param-sweep-matrix] {case['key']} -> {row.get('classification')}",
            flush=True,
        )

    _write_summary(rows)
    counts = {}
    for row in rows:
        key = row.get("classification") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    print(json.dumps({"total_cases": len(rows), "classification_counts": dict(sorted(counts.items())), "summary_path": str(SUMMARY)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
