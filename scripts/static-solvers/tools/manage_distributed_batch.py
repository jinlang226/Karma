#!/usr/bin/env python3
"""Prepare, sync, launch, and inspect distributed static-solver batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from _shared import REPO_ROOT, now_utc_iso, write_json


DEFAULT_REMOTE_ROOT = "/users/jinlang/Karma"
DEFAULT_REMOTE_USER = "jinlang"
DEFAULT_REMOTE_PYTHON = ".venv/bin/python"
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "personal"


@dataclass(frozen=True)
class HostAssignment:
    """Describe one remote host and its shard file."""

    host: str
    shard_rel: str

    @property
    def safe_host(self) -> str:
        """Return the filesystem-safe host slug."""
        return self.host.replace(".", "-")

    def shard_path(self, batch_dir: Path) -> Path:
        """Resolve the local shard path for this assignment."""
        direct = batch_dir / self.shard_rel
        if direct.exists():
            return direct
        fallback = batch_dir / "shards" / Path(self.shard_rel).name
        if fallback.exists():
            return fallback
        return direct


def _workflow_list(path: Path) -> list[str]:
    """Load a newline-delimited workflow list."""
    items: list[str] = []
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        items.append(line)
    return items


def _host_list(path: Path) -> list[str]:
    """Load hosts from a JSON mapping or JSON list."""
    payload = json.loads(path.read_text())
    if isinstance(payload, dict):
        return list(payload.keys())
    if isinstance(payload, list):
        return [str(item) for item in payload]
    raise ValueError(f"Unsupported host manifest format: {path}")


def _repo_rel(path: Path) -> Path:
    """Return a path relative to the repository root."""
    return path.resolve().relative_to(REPO_ROOT)


def _batch_rel(batch_dir: Path) -> str:
    """Return the batch directory relative to the repository root."""
    return _repo_rel(batch_dir).as_posix()


def _load_assignments(batch_dir: Path) -> list[HostAssignment]:
    """Load host assignments and normalize shard paths."""
    raw = json.loads((batch_dir / "host-assignments.json").read_text())
    assignments: list[HostAssignment] = []
    for host, shard_value in raw.items():
        shard_rel = str(shard_value)
        candidate = batch_dir / shard_rel
        if not candidate.exists():
            fallback = batch_dir / "shards" / Path(shard_rel).name
            if fallback.exists():
                shard_rel = fallback.relative_to(batch_dir).as_posix()
        assignments.append(HostAssignment(host=str(host), shard_rel=shard_rel))
    return assignments


def _ssh_base(key_path: Path) -> list[str]:
    """Build the shared SSH command prefix."""
    return [
        "ssh",
        "-i",
        str(key_path),
        "-o",
        "StrictHostKeyChecking=no",
    ]


def _scp_base(key_path: Path) -> list[str]:
    """Build the shared SCP command prefix."""
    return [
        "scp",
        "-i",
        str(key_path),
        "-o",
        "StrictHostKeyChecking=no",
    ]


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and capture text output."""
    return subprocess.run(args, text=True, capture_output=True, check=check)


def _ssh(
    host: str,
    remote_command: str,
    *,
    key_path: Path,
    remote_user: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run one remote shell command over SSH."""
    return _run(_ssh_base(key_path) + [f"{remote_user}@{host}", remote_command], check=check)


def _group_paths(paths: list[Path]) -> dict[Path, list[Path]]:
    """Group relative paths by their parent directory."""
    grouped: dict[Path, list[Path]] = {}
    for rel_path in paths:
        grouped.setdefault(rel_path.parent, []).append(rel_path)
    return grouped


def _sha256(path: Path) -> str:
    """Compute a file SHA256."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_batch(args: argparse.Namespace) -> int:
    """Generate shards and host-assignment metadata for a batch."""
    batch_dir = Path(args.batch_dir).resolve()
    workflow_list_path = Path(args.workflow_list).resolve()
    hosts_path = Path(args.hosts_json).resolve()
    excluded = {host.strip() for host in args.exclude_host if host.strip()}

    workflows = _workflow_list(workflow_list_path)
    hosts = [host for host in _host_list(hosts_path) if host not in excluded]
    if not workflows:
        raise SystemExit("workflow list is empty")
    if not hosts:
        raise SystemExit("no hosts available after exclusions")

    shards_dir = batch_dir / "shards"
    batch_dir.mkdir(parents=True, exist_ok=True)
    shards_dir.mkdir(parents=True, exist_ok=True)

    assignments: dict[str, list[str]] = {host: [] for host in hosts}
    for index, workflow in enumerate(workflows):
        assignments[hosts[index % len(hosts)]].append(workflow)

    host_payload: dict[str, str] = {}
    summary: dict[str, Any] = {
        "generated_at": now_utc_iso(),
        "workflow_total": len(workflows),
        "hosts_total": len(hosts),
        "excluded_hosts": sorted(excluded),
        "shards": [],
    }
    active_shards: set[str] = set()

    for index, host in enumerate(hosts, start=1):
        shard_rel = f"shards/shard-{index:02d}.txt"
        shard_path = batch_dir / shard_rel
        shard_workflows = assignments[host]
        shard_path.write_text("".join(f"{workflow}\n" for workflow in shard_workflows))
        host_payload[host] = shard_rel
        active_shards.add(Path(shard_rel).name)
        summary["shards"].append(
            {
                "host": host,
                "shard": shard_rel,
                "workflow_count": len(shard_workflows),
                "workflows": shard_workflows,
            }
        )

    for stale in sorted(shards_dir.glob("shard-*.txt")):
        if stale.name not in active_shards:
            stale.unlink()

    (batch_dir / "all-workflows.txt").write_text("".join(f"{workflow}\n" for workflow in workflows))
    write_json(batch_dir / "host-assignments.json", host_payload)
    write_json(batch_dir / "shard-summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


def _batch_files(batch_dir: Path, assignment: HostAssignment) -> list[Path]:
    """Return the batch metadata and shard files needed for one host."""
    files = [
        _repo_rel(batch_dir / "all-workflows.txt"),
        _repo_rel(batch_dir / "host-assignments.json"),
        _repo_rel(batch_dir / "shard-summary.json"),
        _repo_rel(assignment.shard_path(batch_dir)),
    ]
    return files


def _sync_host(
    assignment: HostAssignment,
    *,
    batch_dir: Path,
    sync_files: list[Path],
    key_path: Path,
    remote_root: str,
    remote_user: str,
    clean_batch: bool,
    verify: bool,
) -> dict[str, Any]:
    """Sync one host and optionally verify the copied files."""
    batch_rel = _batch_rel(batch_dir)
    remote_batch = f"{remote_root}/{batch_rel}"
    remote_paths = sync_files + _batch_files(batch_dir, assignment)
    grouped = _group_paths(remote_paths)

    if clean_batch:
        kill_and_reset = (
            "set -euo pipefail; "
            f"cd {shlex.quote(remote_root)}; "
            f"if [ -f {shlex.quote(batch_rel + '/runner.pid')} ]; then "
            f"pid=$(cat {shlex.quote(batch_rel + '/runner.pid')} 2>/dev/null || true); "
            "if [ -n \"$pid\" ] && ps -p \"$pid\" >/dev/null 2>&1; then kill \"$pid\"; fi; "
            "fi; "
            f"rm -rf {shlex.quote(remote_batch)}; "
            f"mkdir -p {shlex.quote(remote_batch)}"
        )
        _ssh(assignment.host, kill_and_reset, key_path=key_path, remote_user=remote_user)

    base_dirs = {
        batch_rel,
        f"{batch_rel}/logs",
        f"{batch_rel}/results",
        f"{batch_rel}/summaries",
        f"{batch_rel}/batch/{assignment.safe_host}",
        f"{batch_rel}/shards",
    }
    mkdir_cmd = "mkdir -p " + " ".join(shlex.quote(f"{remote_root}/{item}") for item in sorted(base_dirs))
    _ssh(assignment.host, mkdir_cmd, key_path=key_path, remote_user=remote_user)

    for parent, rel_paths in grouped.items():
        remote_dir = f"{remote_root}/{parent.as_posix()}"
        _ssh(
            assignment.host,
            f"mkdir -p {shlex.quote(remote_dir)}",
            key_path=key_path,
            remote_user=remote_user,
        )
        local_files = [str(REPO_ROOT / rel_path) for rel_path in rel_paths]
        _run(
            _scp_base(key_path) + local_files + [f"{remote_user}@{assignment.host}:{remote_dir}/"],
            check=True,
        )

    verification: dict[str, Any] = {
        "host": assignment.host,
        "safe_host": assignment.safe_host,
        "copied_files": [path.as_posix() for path in remote_paths],
        "verified": False,
        "mismatches": [],
    }
    if not verify:
        return verification

    rel_payload = json.dumps([path.as_posix() for path in remote_paths], sort_keys=True)
    remote_script = f"""
import hashlib
import json
import os
from pathlib import Path

repo_root = Path({remote_root!r})
paths = json.loads(os.environ["PATHS_JSON"])
payload = {{}}
for rel in paths:
    path = repo_root / rel
    if not path.exists():
        payload[rel] = None
        continue
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    payload[rel] = digest.hexdigest()
print(json.dumps(payload, sort_keys=True))
"""
    verify_cmd = (
        f"cd {shlex.quote(remote_root)} && "
        f"PATHS_JSON={shlex.quote(rel_payload)} python3 - <<'PY'\n{remote_script}\nPY"
    )
    proc = _ssh(assignment.host, verify_cmd, key_path=key_path, remote_user=remote_user)
    remote_hashes = json.loads(proc.stdout)

    mismatches: list[dict[str, Any]] = []
    for rel_path in remote_paths:
        rel_text = rel_path.as_posix()
        local_hash = _sha256(REPO_ROOT / rel_path)
        remote_hash = remote_hashes.get(rel_text)
        if local_hash != remote_hash:
            mismatches.append(
                {
                    "path": rel_text,
                    "local_sha256": local_hash,
                    "remote_sha256": remote_hash,
                }
            )

    verification["verified"] = not mismatches
    verification["mismatches"] = mismatches
    return verification


def _status_host(
    assignment: HostAssignment,
    *,
    batch_dir: Path,
    key_path: Path,
    remote_root: str,
    remote_user: str,
) -> dict[str, Any]:
    """Collect one host's remote batch status."""
    batch_rel = _batch_rel(batch_dir)
    remote_script = f"""
import json
import os
import subprocess
from pathlib import Path

repo_root = Path({remote_root!r})
batch_rel = {batch_rel!r}
safe_host = {assignment.safe_host!r}
shard_rel = {assignment.shard_rel!r}

batch_dir = repo_root / batch_rel
runner_pid_path = batch_dir / "runner.pid"
summary_path = batch_dir / "summaries" / f"{{safe_host}}.json"
results_path = batch_dir / "results" / f"{{safe_host}}.jsonl"
log_path = batch_dir / "logs" / f"{{safe_host}}.log"
shard_path = batch_dir / shard_rel

payload = {{
    "host": {assignment.host!r},
    "safe_host": safe_host,
    "shard_rel": shard_rel,
    "shard_exists": shard_path.exists(),
    "runner_pid": "",
    "running": False,
    "runner_cmd": "",
    "summary_exists": summary_path.exists(),
    "results_exists": results_path.exists(),
    "results_lines": 0,
    "counts": {{}},
    "total_records": 0,
    "log_tail": [],
}}

if runner_pid_path.exists():
    pid = runner_pid_path.read_text().strip()
    payload["runner_pid"] = pid
    if pid.isdigit():
        proc = subprocess.run(["ps", "-p", pid, "-o", "args="], capture_output=True, text=True)
        payload["runner_cmd"] = proc.stdout.strip()
        payload["running"] = proc.returncode == 0 and "run_candidate_workflows.py" in payload["runner_cmd"]

if results_path.exists():
    payload["results_lines"] = sum(1 for line in results_path.read_text().splitlines() if line.strip())

if summary_path.exists():
    summary = json.loads(summary_path.read_text())
    payload["counts"] = summary.get("counts") or {{}}
    payload["total_records"] = int(summary.get("total_records") or 0)

if log_path.exists():
    payload["log_tail"] = log_path.read_text(errors="replace").splitlines()[-3:]

print(json.dumps(payload, sort_keys=True))
"""
    proc = _ssh(
        assignment.host,
        f"cd {shlex.quote(remote_root)} && python3 - <<'PY'\n{remote_script}\nPY",
        key_path=key_path,
        remote_user=remote_user,
    )
    return json.loads(proc.stdout)


def _sync_batch(args: argparse.Namespace) -> int:
    """Sync the latest scripts and batch files to every assigned host."""
    batch_dir = Path(args.batch_dir).resolve()
    key_path = Path(args.ssh_key).expanduser()
    sync_files = [_repo_rel(Path(path).resolve()) for path in args.sync_file]
    results = []
    for assignment in _load_assignments(batch_dir):
        result = _sync_host(
            assignment,
            batch_dir=batch_dir,
            sync_files=sync_files,
            key_path=key_path,
            remote_root=args.remote_root,
            remote_user=args.remote_user,
            clean_batch=args.clean_batch,
            verify=not args.no_verify,
        )
        results.append(result)
    payload = {"generated_at": now_utc_iso(), "results": results}
    print(json.dumps(payload, indent=2))
    return 0 if all(result["verified"] or args.no_verify for result in results) else 1


def _stop_remote_runner(
    assignment: HostAssignment,
    *,
    batch_dir: Path,
    key_path: Path,
    remote_root: str,
    remote_user: str,
) -> None:
    """Stop one existing batch runner if its runner.pid still points at a live process."""
    batch_rel = _batch_rel(batch_dir)
    remote_cmd = (
        "set -euo pipefail; "
        f"cd {shlex.quote(remote_root)}; "
        f"pid_file={shlex.quote(batch_rel + '/runner.pid')}; "
        "if [ -f \"$pid_file\" ]; then "
        "pid=$(cat \"$pid_file\" 2>/dev/null || true); "
        "if [ -n \"$pid\" ] && ps -p \"$pid\" >/dev/null 2>&1; then kill \"$pid\"; fi; "
        "fi"
    )
    _ssh(assignment.host, remote_cmd, key_path=key_path, remote_user=remote_user, check=True)


def _launch_batch(args: argparse.Namespace) -> int:
    """Sync and launch one batch worker per assigned host."""
    batch_dir = Path(args.batch_dir).resolve()
    key_path = Path(args.ssh_key).expanduser()
    sync_files = [_repo_rel(Path(path).resolve()) for path in args.sync_file]
    assignments = _load_assignments(batch_dir)
    batch_rel = _batch_rel(batch_dir)
    remote_python = f"{args.remote_root}/{args.remote_python}".replace("//", "/")
    launch_results: list[dict[str, Any]] = []

    for assignment in assignments:
        shard_workflow_count = len(_workflow_list(assignment.shard_path(batch_dir)))
        sync_result = _sync_host(
            assignment,
            batch_dir=batch_dir,
            sync_files=sync_files,
            key_path=key_path,
            remote_root=args.remote_root,
            remote_user=args.remote_user,
            clean_batch=args.clean_batch,
            verify=not args.no_verify,
        )
        if not args.no_verify and not sync_result["verified"]:
            raise SystemExit(f"sync verification failed on {assignment.host}")
        _stop_remote_runner(
            assignment,
            batch_dir=batch_dir,
            key_path=key_path,
            remote_root=args.remote_root,
            remote_user=args.remote_user,
        )
        prelaunch_status = _status_host(
            assignment,
            batch_dir=batch_dir,
            key_path=key_path,
            remote_root=args.remote_root,
            remote_user=args.remote_user,
        )
        if (
            args.resume
            and not args.clean_batch
            and int(prelaunch_status.get("total_records") or 0) >= shard_workflow_count
            and not prelaunch_status.get("running")
        ):
            launch_results.append(
                {
                    "host": assignment.host,
                    "launcher_pid": "",
                    "sync_verified": sync_result["verified"],
                    "status": prelaunch_status,
                    "skipped_complete": True,
                }
            )
            continue

        workflow_list_rel = f"{batch_rel}/{assignment.shard_rel}"
        batch_host_rel = f"{batch_rel}/batch/{assignment.safe_host}"
        results_rel = f"{batch_rel}/results/{assignment.safe_host}.jsonl"
        summary_rel = f"{batch_rel}/summaries/{assignment.safe_host}.json"
        log_rel = f"{batch_rel}/logs/{assignment.safe_host}.log"
        pid_rel = f"{batch_rel}/runner.pid"
        resume_arg = " --resume" if args.resume else ""
        remote_launch = (
            "set -euo pipefail; "
            f"cd {shlex.quote(args.remote_root)}; "
            f"nohup bash -lc {shlex.quote(f'cd {args.remote_root} && echo $$ > {pid_rel} && exec {remote_python} scripts/static-solvers/tools/run_candidate_workflows.py --workflow-list {workflow_list_rel} --batch-dir {batch_host_rel} --results-path {results_rel} --summary-path {summary_rel}{resume_arg}')} "
            f"> {shlex.quote(log_rel)} 2>&1 < /dev/null & "
            "printf '%s\\n' \"$!\""
        )
        launch_proc = _ssh(
            assignment.host,
            remote_launch,
            key_path=key_path,
            remote_user=args.remote_user,
        )
        time.sleep(args.launch_settle_sec)
        status = _status_host(
            assignment,
            batch_dir=batch_dir,
            key_path=key_path,
            remote_root=args.remote_root,
            remote_user=args.remote_user,
        )
        launched = bool((launch_proc.stdout or "").strip())
        if not status.get("running"):
            raise SystemExit(f"launch verification failed on {assignment.host}: {json.dumps(status, indent=2)}")
        launch_results.append(
            {
                "host": assignment.host,
                "launcher_pid": (launch_proc.stdout or "").strip(),
                "sync_verified": sync_result["verified"],
                "status": status,
            }
        )

    payload = {"generated_at": now_utc_iso(), "results": launch_results}
    print(json.dumps(payload, indent=2))
    return 0


def _status_batch(args: argparse.Namespace) -> int:
    """Read aggregate status across the assigned hosts."""
    batch_dir = Path(args.batch_dir).resolve()
    key_path = Path(args.ssh_key).expanduser()
    assignments = _load_assignments(batch_dir)
    host_rows = [
        _status_host(
            assignment,
            batch_dir=batch_dir,
            key_path=key_path,
            remote_root=args.remote_root,
            remote_user=args.remote_user,
        )
        for assignment in assignments
    ]

    aggregate_counts: dict[str, int] = {}
    completed_records = 0
    running_hosts = 0
    launched_hosts = 0
    for row in host_rows:
        if row.get("runner_pid") or row.get("running") or row.get("summary_exists") or row.get("results_exists"):
            launched_hosts += 1
        if row.get("summary_exists"):
            completed_records += int(row.get("total_records") or 0)
            for key, value in (row.get("counts") or {}).items():
                aggregate_counts[key] = aggregate_counts.get(key, 0) + int(value)
        if row.get("running"):
            running_hosts += 1

    payload = {
        "generated_at": now_utc_iso(),
        "batch_dir": str(batch_dir),
        "workflow_total": sum(
            len(_workflow_list(assignment.shard_path(batch_dir))) for assignment in assignments
        ),
        "assigned_hosts": len(assignments),
        "launched_hosts": launched_hosts,
        "running_hosts": running_hosts,
        "completed_records": completed_records,
        "aggregate_counts": dict(sorted(aggregate_counts.items())),
        "host_rows": host_rows,
    }
    print(json.dumps(payload, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser."""
    parser = argparse.ArgumentParser(description="Manage distributed static-solver batches.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Generate shards and host assignments.")
    prepare.add_argument("--batch-dir", required=True)
    prepare.add_argument("--workflow-list", required=True)
    prepare.add_argument("--hosts-json", required=True)
    prepare.add_argument("--exclude-host", action="append", default=[])
    prepare.set_defaults(func=_prepare_batch)

    for name, help_text, func in (
        ("sync", "Sync files and batch metadata to all hosts.", _sync_batch),
        ("launch", "Sync and launch the batch on all hosts.", _launch_batch),
        ("status", "Inspect the current per-host batch status.", _status_batch),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--batch-dir", required=True)
        sub.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
        sub.add_argument("--remote-user", default=DEFAULT_REMOTE_USER)
        sub.add_argument("--ssh-key", default=str(DEFAULT_SSH_KEY))
        if name in {"sync", "launch"}:
            sub.add_argument("--sync-file", action="append", default=[])
            sub.add_argument("--no-verify", action="store_true")
        if name == "sync":
            sub.add_argument("--clean-batch", action="store_true")
        if name == "launch":
            sub.add_argument("--remote-python", default=DEFAULT_REMOTE_PYTHON)
            sub.add_argument("--resume", action="store_true")
            sub.add_argument("--clean-batch", action="store_true")
            sub.add_argument("--launch-settle-sec", type=float, default=2.0)
        sub.set_defaults(func=func)

    return parser


def main() -> int:
    """Run the distributed-batch manager."""
    parser = _build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
