#!/usr/bin/env python3
"""Run a workflow queue across multiple kubeconfigs with resume support.

This helper is intentionally runtime-light: it shells out to
``orchestrator.py run-workflow`` and records one JSONL result per workflow so a
campaign can be resumed safely after process or host failure.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[2]
HEAVY_SERVICES = ("cockroachdb", "elasticsearch", "mongodb", "ray", "spark")
PROTECTED_NAMESPACES = {
    "default",
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "local-path-storage",
}


def now_utc_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 form."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_parent(path: Path) -> None:
    """Create *path*'s parent directory when missing."""

    path.parent.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write *payload* to *path* atomically."""

    ensure_parent(path)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    temp_path.replace(path)


def load_workflow_list(path: Path) -> List[str]:
    """Load a newline-delimited workflow list and preserve first-seen order."""

    seen = set()
    items: List[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line in seen:
            continue
        seen.add(line)
        items.append(line)
    return items


def load_existing_results(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load the last record for each workflow from an existing JSONL results file."""

    if not path.exists():
        return {}
    existing: Dict[str, Dict[str, Any]] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        workflow = str(payload.get("workflow") or "")
        if workflow:
            existing[workflow] = payload
    return existing


def append_result(path: Path, record: Dict[str, Any]) -> None:
    """Append one JSON record to *path* as a JSONL line."""

    ensure_parent(path)
    with path.open("a") as handle:
        handle.write(json.dumps(record, sort_keys=False) + "\n")


def resolve_path(path_value: str) -> Path:
    """Resolve *path_value* relative to the repository root when needed."""

    path = Path(path_value)
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def resolve_workflow_arg(workflow_rel: str) -> str:
    """Return a run-workflow path that exists in the repository.

    Accept both ``workflows/pass/foo.yaml`` and the shorter ``pass/foo.yaml``
    style used by the workflow inventory and sharded batch lists.
    """

    direct = resolve_path(workflow_rel)
    if direct.exists():
        return str(direct.relative_to(REPO_ROOT))
    prefixed = resolve_path(str(Path("workflows") / workflow_rel))
    if prefixed.exists():
        return str(prefixed.relative_to(REPO_ROOT))
    return workflow_rel


def workflow_is_heavy(workflow_rel: str) -> bool:
    """Return whether *workflow_rel* should count against the heavy semaphore."""

    normalized = workflow_rel.replace("\\", "/").lower()
    if "/long/" in normalized:
        return True
    return any("/{0}-".format(service) in normalized or normalized.startswith(service + "-")
               or "/" + service + "/" in normalized
               for service in HEAVY_SERVICES)


def sanitize_name(value: str) -> str:
    """Return a filesystem-safe slug for *value*."""

    out: List[str] = []
    for ch in value:
        if ch.isalnum():
            out.append(ch.lower())
        else:
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "item"


def tail(text: str, limit: int = 4000) -> str:
    """Return at most the last *limit* characters of *text*."""

    if len(text) <= limit:
        return text
    return text[-limit:]


def parse_result(stdout_text: str) -> Optional[Dict[str, Any]]:
    """Parse the JSON result emitted by ``run-workflow --output json``."""

    stripped = stdout_text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def run_command(command: List[str], *, env: Dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run *command* in the repository root and capture text output."""

    return subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def run_kubectl(kubeconfig: str, args: List[str], *, env: Dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run ``kubectl`` with the given *kubeconfig* and argument list."""

    return run_command(["kubectl", "--kubeconfig", kubeconfig] + args, env=env)


def list_namespaces(kubeconfig: str, *, env: Dict[str, str]) -> List[str]:
    """Return the current namespace names for *kubeconfig*."""

    proc = run_kubectl(kubeconfig, ["get", "ns", "-o", "json"], env=env)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "kubectl get ns failed")
    payload = json.loads(proc.stdout or "{}")
    items = payload.get("items") or []
    return sorted(
        str(item.get("metadata", {}).get("name") or "")
        for item in items
        if str(item.get("metadata", {}).get("name") or "")
    )


def extra_namespaces(kubeconfig: str, *, env: Dict[str, str]) -> List[str]:
    """Return non-system namespaces currently present on the cluster."""

    return [name for name in list_namespaces(kubeconfig, env=env) if name not in PROTECTED_NAMESPACES]


def cluster_nodes_ready(kubeconfig: str, *, env: Dict[str, str]) -> tuple[bool, List[str], str]:
    """Return whether all nodes are Ready plus their names and a failure detail."""

    proc = run_kubectl(kubeconfig, ["get", "nodes", "-o", "json"], env=env)
    if proc.returncode != 0:
        return False, [], proc.stderr.strip() or proc.stdout.strip() or "kubectl get nodes failed"
    payload = json.loads(proc.stdout or "{}")
    items = payload.get("items") or []
    if not items:
        return False, [], "no nodes returned by kubectl get nodes"
    node_names: List[str] = []
    not_ready: List[str] = []
    for item in items:
        name = str(item.get("metadata", {}).get("name") or "")
        if name:
            node_names.append(name)
        conditions = item.get("status", {}).get("conditions") or []
        ready = any(
            cond.get("type") == "Ready" and cond.get("status") == "True"
            for cond in conditions
        )
        if name and not ready:
            not_ready.append(name)
    if not_ready:
        return False, node_names, "nodes not ready: " + ", ".join(not_ready)
    return True, node_names, ""


def cleanup_namespaces(
    kubeconfig: str,
    *,
    env: Dict[str, str],
    timeout_sec: int,
) -> Dict[str, Any]:
    """Delete non-system namespaces and wait for them to disappear."""

    before = extra_namespaces(kubeconfig, env=env)
    if not before:
        return {"before": [], "deleted": [], "remaining": [], "timed_out": False}
    for namespace in before:
        run_kubectl(kubeconfig, ["delete", "namespace", namespace, "--wait=false"], env=env)
    deadline = time.monotonic() + max(1, timeout_sec)
    remaining = before
    while time.monotonic() < deadline:
        remaining = [name for name in extra_namespaces(kubeconfig, env=env) if name in before]
        if not remaining:
            return {"before": before, "deleted": before, "remaining": [], "timed_out": False}
        time.sleep(2.0)
    return {"before": before, "deleted": before, "remaining": remaining, "timed_out": True}


def environment_preflight(args: argparse.Namespace, kubeconfig: str, *, env: Dict[str, str]) -> Dict[str, Any]:
    """Verify cluster readiness and clean leftovers before a workflow starts."""

    kubeconfig_path = Path(kubeconfig)
    if not kubeconfig_path.exists():
        return {
            "ok": False,
            "reason": f"kubeconfig not found: {kubeconfig}",
            "kubeconfig": kubeconfig,
        }
    ready, node_names, detail = cluster_nodes_ready(kubeconfig, env=env)
    if not ready:
        return {
            "ok": False,
            "reason": detail or "cluster nodes not ready",
            "kubeconfig": kubeconfig,
            "nodes": node_names,
        }
    cleanup = cleanup_namespaces(
        kubeconfig,
        env=env,
        timeout_sec=args.namespace_cleanup_timeout,
    )
    if cleanup.get("timed_out"):
        return {
            "ok": False,
            "reason": "namespace cleanup timed out",
            "kubeconfig": kubeconfig,
            "nodes": node_names,
            "cleanup": cleanup,
        }
    return {
        "ok": True,
        "reason": "",
        "kubeconfig": kubeconfig,
        "nodes": node_names,
        "cleanup": cleanup,
    }


def build_command(args: argparse.Namespace, workflow_rel: str) -> List[str]:
    """Construct the ``orchestrator.py run-workflow`` command for one workflow."""

    runtime_python = args.runtime_python
    runtime_python_path = Path(runtime_python)
    if not runtime_python_path.is_absolute():
        runtime_python_path = REPO_ROOT / runtime_python_path
    command = [
        str(runtime_python_path),
        "orchestrator.py",
        "run-workflow",
        resolve_workflow_arg(workflow_rel),
        "--agent",
        args.agent,
        "--sandbox",
        args.sandbox,
        "--runs-dir",
        args.runs_dir,
        "--resources-dir",
        args.resources_dir,
        "--output",
        "json",
    ]
    if args.llm_env_file:
        command += ["--llm-env-file", args.llm_env_file]
    if args.agent_build:
        command.append("--agent-build")
    if args.max_attempts is not None:
        command += ["--max-attempts", str(args.max_attempts)]
    if args.stage_failure_mode:
        command += ["--stage-failure-mode", args.stage_failure_mode]
    if args.final_sweep_mode:
        command += ["--final-sweep-mode", args.final_sweep_mode]
    if args.setup_timeout is not None:
        command += ["--setup-timeout", str(args.setup_timeout)]
    if args.verify_timeout is not None:
        command += ["--verify-timeout", str(args.verify_timeout)]
    return command


def summarize_records(workflows: List[str], records: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Build a compact summary for the current queue state."""

    outcome_counts = Counter()
    run_status_counts = Counter()
    for record in records.values():
        outcome_counts[str(record.get("outcome") or "unknown")] += 1
        run_status = str(record.get("run_status") or "")
        if run_status:
            run_status_counts[run_status] += 1
    return {
        "generated_at": now_utc_iso(),
        "workflow_total": len(workflows),
        "completed": len(records),
        "remaining": max(0, len(workflows) - len(records)),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "run_status_counts": dict(sorted(run_status_counts.items())),
        "records": [records[key] for key in workflows if key in records],
    }


def write_status(
    path: Path,
    *,
    workflows: List[str],
    completed: Dict[str, Dict[str, Any]],
    inflight: Dict[str, str],
) -> None:
    """Persist live queue status for external polling."""

    payload = summarize_records(workflows, completed)
    payload["inflight"] = len([item for item in inflight.values() if item])
    payload["workers"] = dict(sorted(inflight.items()))
    atomic_write_json(path, payload)


def simulate_record(
    workflow_rel: str,
    kubeconfig: str,
    *,
    heavy: bool,
    delay_sec: float,
) -> Dict[str, Any]:
    """Return a synthetic success record for queue smoke tests."""

    started_at = now_utc_iso()
    if delay_sec > 0:
        time.sleep(delay_sec)
    finished_at = now_utc_iso()
    return {
        "workflow": workflow_rel,
        "worker_kubeconfig": kubeconfig,
        "heavy": heavy,
        "mode": "simulate",
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": round(delay_sec, 3),
        "returncode": 0,
        "outcome": "simulated_pass",
        "run_id": "",
        "run_status": "simulated",
        "run_dir": "",
        "stage_total": 0,
        "stage_passed": 0,
        "stage_failed": 0,
        "workflow_passed": True,
        "stdout_log": "",
        "stderr_log": "",
        "stdout_tail": "",
        "stderr_tail": "",
        "error": "",
    }


def run_one_workflow(
    args: argparse.Namespace,
    workflow_rel: str,
    kubeconfig: str,
    *,
    heavy: bool,
    logs_dir: Path,
) -> Dict[str, Any]:
    """Run one workflow and return its ledger record."""

    if args.simulate:
        return simulate_record(
            workflow_rel,
            kubeconfig,
            heavy=heavy,
            delay_sec=args.simulate_delay,
        )

    slug = sanitize_name(workflow_rel)
    stdout_path = logs_dir / (slug + ".stdout.log")
    stderr_path = logs_dir / (slug + ".stderr.log")
    command = build_command(args, workflow_rel)
    env = dict(os.environ)
    env["KUBECONFIG"] = kubeconfig
    if args.copilot_model:
        env["KARMA_COPILOT_AGENT_MODEL"] = args.copilot_model

    preflight = environment_preflight(args, kubeconfig, env=env)
    if not preflight.get("ok"):
        return {
            "workflow": workflow_rel,
            "worker_kubeconfig": kubeconfig,
            "heavy": heavy,
            "mode": "preflight",
            "started_at": now_utc_iso(),
            "finished_at": now_utc_iso(),
            "duration_sec": 0.0,
            "returncode": None,
            "outcome": "env_preflight_failed",
            "run_id": "",
            "run_status": "",
            "run_dir": "",
            "stage_total": 0,
            "stage_passed": 0,
            "stage_failed": 0,
            "workflow_passed": False,
            "stdout_log": "",
            "stderr_log": "",
            "stdout_tail": "",
            "stderr_tail": "",
            "error": str(preflight.get("reason") or "environment preflight failed"),
            "preflight": preflight,
            "post_cleanup": {"before": [], "deleted": [], "remaining": [], "timed_out": False},
        }

    started_at = now_utc_iso()
    started_monotonic = time.monotonic()
    proc = run_command(command, env=env)
    duration_sec = round(time.monotonic() - started_monotonic, 3)
    stdout_path.write_text(proc.stdout or "")
    stderr_path.write_text(proc.stderr or "")
    parsed = parse_result(proc.stdout or "")

    run_id = str((parsed or {}).get("run_id") or "")
    stages = list((parsed or {}).get("stages") or [])
    stage_total = len(stages)
    stage_passed = sum(1 for stage in stages if stage.get("status") == "pass")
    stage_failed = sum(1 for stage in stages if stage.get("status") in ("fail", "error", "timeout"))
    run_status = str((parsed or {}).get("status") or "")
    workflow_passed = bool(
        run_status == "complete"
        and stage_total > 0
        and stage_passed == stage_total
    )
    outcome = "pass" if workflow_passed else "nonpass"
    error_text = ""
    if proc.returncode != 0 and not parsed:
        outcome = "error"
        error_text = "run-workflow returned non-zero without JSON result"
    elif run_status == "error":
        outcome = "error"
        error_text = str((parsed or {}).get("error") or "")

    runs_dir_path = resolve_path(args.runs_dir)
    run_dir = str((runs_dir_path / run_id).resolve()) if run_id else ""
    post_cleanup = cleanup_namespaces(
        kubeconfig,
        env=env,
        timeout_sec=args.namespace_cleanup_timeout,
    )
    return {
        "workflow": workflow_rel,
        "worker_kubeconfig": kubeconfig,
        "heavy": heavy,
        "mode": "orchestrator",
        "started_at": started_at,
        "finished_at": now_utc_iso(),
        "duration_sec": duration_sec,
        "returncode": proc.returncode,
        "outcome": outcome,
        "run_id": run_id,
        "run_status": run_status,
        "run_dir": run_dir,
        "stage_total": stage_total,
        "stage_passed": stage_passed,
        "stage_failed": stage_failed,
        "workflow_passed": workflow_passed,
        "stdout_log": str(stdout_path.relative_to(args.batch_dir_path)),
        "stderr_log": str(stderr_path.relative_to(args.batch_dir_path)),
        "stdout_tail": tail(proc.stdout or ""),
        "stderr_tail": tail(proc.stderr or ""),
        "error": error_text,
        "preflight": preflight,
        "post_cleanup": post_cleanup,
    }


def parse_args() -> argparse.Namespace:
    """Parse CLI flags for the queue runner."""

    parser = argparse.ArgumentParser(description="Run workflow queues with resume support.")
    parser.add_argument("--workflow-list", required=True, help="Newline-delimited workflow list.")
    parser.add_argument("--kubeconfigs", required=True, help="Comma-separated kubeconfig paths or labels.")
    parser.add_argument("--batch-dir", required=True, help="Campaign directory for logs and ledgers.")
    parser.add_argument("--runtime-python", default=".venv/bin/python",
                        help="Python interpreter used for orchestrator.py on the target host.")
    parser.add_argument("--agent", default="copilot")
    parser.add_argument("--sandbox", default="docker", choices=["local", "docker"])
    parser.add_argument("--runs-dir", default="runs/copilot-remote")
    parser.add_argument("--resources-dir", default="cases")
    parser.add_argument("--llm-env-file", default="")
    parser.add_argument("--copilot-model", default="")
    parser.add_argument("--max-heavy", type=int, default=3)
    parser.add_argument("--max-attempts", type=int, default=None)
    parser.add_argument("--stage-failure-mode", default="terminate", choices=["terminate", "continue"])
    parser.add_argument("--final-sweep-mode", default="auto", choices=["auto", "off", "full"])
    parser.add_argument("--setup-timeout", type=int, default=None)
    parser.add_argument("--verify-timeout", type=int, default=None)
    parser.add_argument("--agent-build", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="Skip workflows already present in results.jsonl.")
    parser.add_argument("--simulate", action="store_true",
                        help="Write synthetic pass records instead of calling orchestrator.py.")
    parser.add_argument("--simulate-delay", type=float, default=0.0,
                        help="Optional delay per simulated workflow.")
    parser.add_argument("--namespace-cleanup-timeout", type=int, default=240,
                        help="Seconds to wait for non-system namespaces to delete before/after a workflow.")
    args = parser.parse_args()
    args.batch_dir_path = Path(args.batch_dir).resolve()
    return args


def main() -> int:
    """Execute the queue with one worker per kubeconfig."""

    args = parse_args()
    workflow_list_path = resolve_path(args.workflow_list)
    workflows = load_workflow_list(workflow_list_path)
    if not workflows:
        raise SystemExit("workflow list is empty")

    kubeconfigs = [item.strip() for item in args.kubeconfigs.split(",") if item.strip()]
    if not kubeconfigs:
        raise SystemExit("at least one kubeconfig is required")

    batch_dir = args.batch_dir_path
    results_path = batch_dir / "results.jsonl"
    summary_path = batch_dir / "summary.json"
    status_path = batch_dir / "status.json"
    logs_dir = batch_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    existing = load_existing_results(results_path) if args.resume else {}
    pending = [workflow for workflow in workflows if workflow not in existing]
    task_queue: "queue.Queue[str]" = queue.Queue()
    for workflow in pending:
        task_queue.put(workflow)

    completed = dict(existing)
    inflight = dict((kubeconfig, "") for kubeconfig in kubeconfigs)
    lock = threading.Lock()
    heavy_sem = threading.Semaphore(max(1, args.max_heavy))

    write_status(status_path, workflows=workflows, completed=completed, inflight=inflight)
    atomic_write_json(summary_path, summarize_records(workflows, completed))

    def worker(kubeconfig: str) -> None:
        """Process queued workflows on a single kubeconfig."""

        while True:
            try:
                workflow_rel = task_queue.get_nowait()
            except queue.Empty:
                return
            heavy = workflow_is_heavy(workflow_rel)
            if heavy:
                heavy_sem.acquire()
            try:
                with lock:
                    inflight[kubeconfig] = workflow_rel
                    write_status(status_path, workflows=workflows, completed=completed, inflight=inflight)
                record = run_one_workflow(
                    args,
                    workflow_rel,
                    kubeconfig,
                    heavy=heavy,
                    logs_dir=logs_dir,
                )
                with lock:
                    append_result(results_path, record)
                    completed[workflow_rel] = record
                    inflight[kubeconfig] = ""
                    atomic_write_json(summary_path, summarize_records(workflows, completed))
                    write_status(status_path, workflows=workflows, completed=completed, inflight=inflight)
                print(
                    "[{0}/{1}] {2} -> {3}".format(
                        len(completed),
                        len(workflows),
                        workflow_rel,
                        record.get("outcome"),
                    ),
                    flush=True,
                )
            finally:
                if heavy:
                    heavy_sem.release()
                task_queue.task_done()

    threads = [threading.Thread(target=worker, args=(kubeconfig,), daemon=True) for kubeconfig in kubeconfigs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    atomic_write_json(summary_path, summarize_records(workflows, completed))
    write_status(status_path, workflows=workflows, completed=completed, inflight=inflight)
    print(json.dumps(summarize_records(workflows, completed), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
