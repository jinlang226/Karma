#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


SMOKE_DIR = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = SMOKE_DIR / "examples"
PROFILE = SMOKE_DIR / "local_workflow_profile.yaml"
AGENT = SMOKE_DIR / "repeat_stage_agent.py"
SUMMARY = SMOKE_DIR / "param_sweep_examples_summary.json"
PYTHON = ROOT / ".venv" / "bin" / "python"

EXAMPLES = {
    "repeat_demo_configmap_value_sweep": {
        "workflow": "repeat_demo_configmap_value_sweep.yaml",
        "solver": "demo_configmap_update",
    },
    "repeat_demo_configmap_two_ns_value_sweep": {
        "workflow": "repeat_demo_configmap_two_ns_value_sweep.yaml",
        "solver": "demo_configmap_update_two_ns",
    },
    "repeat_nginx_route_param_sweep": {
        "workflow": "repeat_nginx_route_param_sweep.yaml",
        "solver": "nginx_route",
    },
    "repeat_nginx_route": {
        "workflow": "repeat_nginx_route.yaml",
        "solver": "nginx_route",
    },
    "repeat_ray_dashboard": {
        "workflow": "repeat_ray_dashboard.yaml",
        "solver": "ray_dashboard",
    },
    "repeat_ray_job_execution_param_sweep": {
        "workflow": "repeat_ray_job_execution_param_sweep.yaml",
        "solver": "ray_job_execution",
    },
    "repeat_ray_worker_scaling": {
        "workflow": "repeat_ray_worker_scaling.yaml",
        "solver": "ray_worker_scale",
    },
    "repeat_ray_worker_scaling_param_sweep": {
        "workflow": "repeat_ray_worker_scaling_param_sweep.yaml",
        "solver": "ray_worker_scale",
    },
    "repeat_spark_pi": {
        "workflow": "repeat_spark_pi.yaml",
        "solver": "spark_pi",
    },
    "repeat_spark_pi_param_sweep": {
        "workflow": "repeat_spark_pi_param_sweep.yaml",
        "solver": "spark_pi",
    },
}


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
    return PYTHON if PYTHON.exists() else Path(sys.executable)


def _profile_sandbox() -> str:
    try:
        data = yaml.safe_load(PROFILE.read_text(encoding="utf-8")) or {}
    except Exception:
        return ""
    return str(data.get("sandbox") or "").strip().lower()


def _resolve_direct_proxy_server(source_kubeconfig: str) -> str:
    env = os.environ.copy()
    if source_kubeconfig:
        env["KUBECONFIG"] = source_kubeconfig
    try:
        raw = subprocess.check_output(
            ["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"],
            text=True,
            env=env,
        ).strip()
    except Exception:
        return ""
    if not raw:
        return ""
    raw = raw.replace("https://", "").replace("http://", "")
    return raw.split("/", 1)[0]


def _launcher_env_and_args() -> tuple[dict[str, str], list[str]]:
    env = os.environ.copy()
    extra_args: list[str] = []
    source_kubeconfig = (env.get("KUBECONFIG") or "").strip()
    proxy_server = (env.get("BENCHMARK_PROXY_SERVER") or "").strip()
    if not proxy_server and _profile_sandbox() == "local":
        proxy_server = _resolve_direct_proxy_server(source_kubeconfig)
    if source_kubeconfig:
        extra_args.extend(["--source-kubeconfig", source_kubeconfig])
    if proxy_server:
        extra_args.extend(["--proxy-server", proxy_server])
    return env, extra_args


def _selected(filters: list[str]) -> list[tuple[str, dict]]:
    if not filters:
        return sorted(EXAMPLES.items())
    wanted = set(filters)
    rows = []
    for key, meta in sorted(EXAMPLES.items()):
        workflow_name = Path(meta["workflow"]).stem
        if key in wanted or workflow_name in wanted or meta["workflow"] in wanted:
            rows.append((key, meta))
    return rows


def _run_one(key: str, meta: dict, *, submit_timeout: str, setup_timeout: str, verify_timeout: str, cleanup_timeout: str) -> dict:
    python_exec = _python_exec()
    workflow_path = EXAMPLES_DIR / meta["workflow"]
    env, launcher_args = _launcher_env_and_args()
    cmd = [
        str(python_exec),
        "orchestrator.py",
        "workflow-run",
        "--profile",
        str(PROFILE),
        "--workflow",
        str(workflow_path),
        "--agent-cmd",
        f"python3 {AGENT} --solver {meta['solver']}",
        "--submit-timeout",
        submit_timeout,
        "--setup-timeout",
        setup_timeout,
        "--setup-timeout-mode",
        "auto",
        "--verify-timeout",
        verify_timeout,
        "--cleanup-timeout",
        cleanup_timeout,
        "--max-attempts",
        "1",
        "--stage-failure-mode",
        "terminate",
    ]
    cmd.extend(launcher_args)
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    row = {
        "name": key,
        "workflow": str(workflow_path.relative_to(ROOT)),
        "solver": meta["solver"],
        "command": cmd,
        "returncode": proc.returncode,
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-120:]),
    }
    if proc.returncode == 0:
        payload = _parse_payload(proc.stdout)
        result = (payload[0] or {}).get("result") or {}
        row["result"] = result
        row["status"] = result.get("status")
    else:
        row["status"] = "runner_error"
    return row


def _write_summary(rows: list[dict]) -> None:
    SUMMARY.write_text(json.dumps({"examples": rows}, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("filters", nargs="*")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--submit-timeout", default="900")
    parser.add_argument("--setup-timeout", default="600")
    parser.add_argument("--verify-timeout", default="600")
    parser.add_argument("--cleanup-timeout", default="600")
    args = parser.parse_args()

    selected = _selected(args.filters)
    if args.filters and not selected:
        print(f"no example workflows matched filters: {args.filters}", file=sys.stderr)
        return 1

    if args.list:
        for key, meta in selected:
            print(f"{key}\t{meta['workflow']}\t{meta['solver']}")
        return 0

    SUMMARY.unlink(missing_ok=True)
    rows = []
    for key, meta in selected:
        print(f"[param-sweeps] running {key}", flush=True)
        row = _run_one(
            key,
            meta,
            submit_timeout=args.submit_timeout,
            setup_timeout=args.setup_timeout,
            verify_timeout=args.verify_timeout,
            cleanup_timeout=args.cleanup_timeout,
        )
        rows.append(row)
        _write_summary(rows)
        print(f"[param-sweeps] {key} -> {row.get('status')}", flush=True)

    _write_summary(rows)
    print(json.dumps({"examples": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
