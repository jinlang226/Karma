#!/usr/bin/env python3
"""Prepare, sync, preflight, launch, and monitor distributed Copilot campaigns."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REMOTE_ROOT = "/users/jinlang/Karma"
DEFAULT_REMOTE_USER = "jinlang"
DEFAULT_REMOTE_PYTHON = ".venv/bin/python"
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "personal"
DEFAULT_REMOTE_ENV = ".benchmark/copilot.env"
DEFAULT_REMOTE_KUBECONFIG = "/tmp/kc-1"
DEFAULT_CLUSTER_NAME = "kind"
QUEUE_RUNNER = REPO_ROOT / "scripts" / "remote-agents" / "run_workflow_queue.py"
COPILOT_DOCKERFILE = REPO_ROOT / "karma" / "agents" / "copilot" / "Dockerfile"
COPILOT_CONTEXT = REPO_ROOT / "karma" / "agents" / "copilot"
PERSISTENT_SESSION_RUNTIME_FILES = (
    REPO_ROOT / "karma" / "definitions" / "workflows.py",
    REPO_ROOT / "karma" / "runtime" / "service.py",
    REPO_ROOT / "karma" / "runtime" / "workflow.py",
    REPO_ROOT / "karma" / "runtime" / "case.py",
)
SYSTEM_PROMPT_SUPPORT_FILES = (
    REPO_ROOT / "karma" / "protocol.py",
    REPO_ROOT / "docs" / "default-system-prompt.md",
)


@dataclass(frozen=True)
class HostSpec:
    """One physical host and the worker profiles it exposes."""

    host: str
    kubeconfigs: tuple[str, ...] = (DEFAULT_REMOTE_KUBECONFIG,)
    cluster_names: tuple[str, ...] = ()

    @property
    def safe_host(self) -> str:
        """Return a filesystem-safe host slug."""

        return self.host.replace(".", "-")

    @property
    def profile_count(self) -> int:
        """Return how many worker profiles this host contributes."""

        return max(1, len(self.kubeconfigs))

    def resolved_profiles(
        self,
        *,
        default_cluster_name: str,
        default_kubeconfig_path: str,
    ) -> List[Dict[str, str]]:
        """Return concrete cluster/kubeconfig pairs for this host."""

        kubeconfigs = tuple(item for item in self.kubeconfigs if item)
        if not kubeconfigs:
            kubeconfigs = (default_kubeconfig_path,)
        if self.cluster_names:
            cluster_names = self.cluster_names
        elif len(kubeconfigs) == 1:
            cluster_names = (default_cluster_name,)
        else:
            cluster_names = tuple("" for _ in kubeconfigs)
        if len(cluster_names) != len(kubeconfigs):
            raise ValueError(
                f"host {self.host} defines {len(cluster_names)} cluster names for "
                f"{len(kubeconfigs)} kubeconfigs"
            )
        return [
            {
                "cluster_name": cluster_names[index],
                "kubeconfig_path": kubeconfigs[index],
            }
            for index in range(len(kubeconfigs))
        ]


@dataclass(frozen=True)
class HostAssignment:
    """One host and its assigned shard file."""

    host: str
    shard_rel: str
    kubeconfigs: tuple[str, ...] = (DEFAULT_REMOTE_KUBECONFIG,)
    cluster_names: tuple[str, ...] = ()

    @property
    def safe_host(self) -> str:
        """Return a filesystem-safe host slug."""

        return self.host.replace(".", "-")

    @property
    def profile_count(self) -> int:
        """Return how many worker profiles this assignment contributes."""

        return max(1, len(self.kubeconfigs))

    def resolved_profiles(
        self,
        *,
        default_cluster_name: str,
        default_kubeconfig_path: str,
    ) -> List[Dict[str, str]]:
        """Return concrete cluster/kubeconfig pairs for this assignment."""

        return HostSpec(
            host=self.host,
            kubeconfigs=self.kubeconfigs,
            cluster_names=self.cluster_names,
        ).resolved_profiles(
            default_cluster_name=default_cluster_name,
            default_kubeconfig_path=default_kubeconfig_path,
        )


def now_utc_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 form."""

    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_parent(path: Path) -> None:
    """Create *path*'s parent directory if needed."""

    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    """Write formatted JSON to *path*."""

    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")


def workflow_list(path: Path) -> List[str]:
    """Load a newline-delimited workflow list."""

    items: List[str] = []
    seen = set()
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line in seen:
            continue
        seen.add(line)
        items.append(line)
    return items


def _string_tuple(items: Any) -> tuple[str, ...]:
    """Return a normalized tuple of non-empty strings."""

    if items is None:
        return ()
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list):
        raise ValueError(f"expected a string or list of strings, got: {type(items).__name__}")
    return tuple(str(item).strip() for item in items if str(item).strip())


def _host_spec_from_payload(host: str, payload: Any) -> HostSpec:
    """Build one host spec from a manifest payload value."""

    kubeconfigs: tuple[str, ...]
    cluster_names: tuple[str, ...]
    if payload is None:
        kubeconfigs = (DEFAULT_REMOTE_KUBECONFIG,)
        cluster_names = ()
    elif isinstance(payload, str):
        kubeconfigs = _string_tuple(payload) or (DEFAULT_REMOTE_KUBECONFIG,)
        cluster_names = ()
    elif isinstance(payload, list):
        kubeconfigs = _string_tuple(payload) or (DEFAULT_REMOTE_KUBECONFIG,)
        cluster_names = ()
    elif isinstance(payload, dict):
        kubeconfigs = _string_tuple(payload.get("kubeconfigs")) or (DEFAULT_REMOTE_KUBECONFIG,)
        cluster_names = _string_tuple(payload.get("cluster_names"))
    else:
        raise ValueError(f"unsupported host manifest entry for {host}: {type(payload).__name__}")
    if cluster_names and len(cluster_names) != len(kubeconfigs):
        raise ValueError(
            f"host {host} defines {len(cluster_names)} cluster names for "
            f"{len(kubeconfigs)} kubeconfigs"
        )
    return HostSpec(
        host=str(host).strip(),
        kubeconfigs=kubeconfigs,
        cluster_names=cluster_names,
    )


def host_specs(path: Path) -> List[HostSpec]:
    """Load physical hosts and worker-profile metadata from JSON."""

    payload = json.loads(path.read_text())
    specs: List[HostSpec] = []
    seen_hosts = set()
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str):
                spec = _host_spec_from_payload(str(item), None)
            elif isinstance(item, dict) and "host" in item:
                host = str(item.get("host") or "").strip()
                if not host:
                    raise ValueError(f"host manifest entry is missing 'host': {item!r}")
                spec = _host_spec_from_payload(host, item)
            else:
                raise ValueError(f"unsupported host manifest list entry: {item!r}")
            if spec.host in seen_hosts:
                raise ValueError(f"duplicate host in manifest: {spec.host}")
            seen_hosts.add(spec.host)
            specs.append(spec)
        return specs
    if isinstance(payload, dict):
        for host, spec_payload in payload.items():
            spec = _host_spec_from_payload(str(host), spec_payload)
            if spec.host in seen_hosts:
                raise ValueError(f"duplicate host in manifest: {spec.host}")
            seen_hosts.add(spec.host)
            specs.append(spec)
        return specs
    raise ValueError(f"unsupported host manifest format: {path}")


def repo_rel(path: Path) -> str:
    """Return *path* relative to the repository root."""

    return path.resolve().relative_to(REPO_ROOT).as_posix()


def batch_rel(batch_dir: Path) -> str:
    """Return *batch_dir* relative to the repository root."""

    return repo_rel(batch_dir)


def resolve_workflow_file(workflow_rel: str) -> Path:
    """Resolve a workflow-list item to its actual repository path."""

    direct = (REPO_ROOT / workflow_rel).resolve()
    if direct.exists():
        return direct
    prefixed = (REPO_ROOT / "workflows" / workflow_rel).resolve()
    if prefixed.exists():
        return prefixed
    raise FileNotFoundError(f"workflow file not found: {workflow_rel}")


def load_assignments(batch_dir: Path) -> List[HostAssignment]:
    """Load host assignments from a prepared batch directory."""

    payload = json.loads((batch_dir / "host-assignments.json").read_text())
    assignments: List[HostAssignment] = []
    for host, shard_payload in payload.items():
        if isinstance(shard_payload, str):
            assignments.append(
                HostAssignment(host=str(host), shard_rel=str(shard_payload))
            )
            continue
        if not isinstance(shard_payload, dict):
            raise ValueError(
                f"unsupported host assignment entry for {host}: "
                f"{type(shard_payload).__name__}"
            )
        shard_rel = str(shard_payload.get("shard") or shard_payload.get("shard_rel") or "").strip()
        if not shard_rel:
            raise ValueError(f"host assignment for {host} is missing shard metadata")
        assignments.append(
            HostAssignment(
                host=str(host),
                shard_rel=shard_rel,
                kubeconfigs=_string_tuple(shard_payload.get("kubeconfigs")) or (DEFAULT_REMOTE_KUBECONFIG,),
                cluster_names=_string_tuple(shard_payload.get("cluster_names")),
            )
        )
    return assignments


def ssh_base(key_path: Path) -> List[str]:
    """Build the shared SSH command prefix."""

    return ["ssh", "-i", str(key_path), "-o", "StrictHostKeyChecking=no"]


def scp_base(key_path: Path) -> List[str]:
    """Build the shared SCP command prefix."""

    return ["scp", "-i", str(key_path), "-o", "StrictHostKeyChecking=no"]


def run(args: List[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and capture output."""

    return subprocess.run(args, text=True, capture_output=True, check=check)


def ssh(host: str, remote_command: str, *, key_path: Path, remote_user: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one remote command over SSH."""

    return run(ssh_base(key_path) + [f"{remote_user}@{host}", remote_command], check=check)


def group_paths(paths: List[Path]) -> Dict[Path, List[Path]]:
    """Group repository-relative paths by parent directory."""

    grouped: Dict[Path, List[Path]] = {}
    for rel_path in paths:
        grouped.setdefault(rel_path.parent, []).append(rel_path)
    return grouped


def campaign_support_files() -> List[Path]:
    """Return repository-relative files every remote Copilot host must receive.

    This includes the queue runner, Copilot container bits, and the runtime /
    workflow-definition files that control persistent cross-stage sessions and
    stage-level system-prompt delivery. CloudLab hosts may have stale local
    clones, so behavior-affecting local files must be synced explicitly instead
    of assumed.
    """

    files = [
        Path(repo_rel(QUEUE_RUNNER)),
        Path(repo_rel(COPILOT_DOCKERFILE)),
        Path(repo_rel(COPILOT_CONTEXT / "entrypoint.sh")),
    ]
    files += [Path(repo_rel(path)) for path in PERSISTENT_SESSION_RUNTIME_FILES]
    files += [Path(repo_rel(path)) for path in SYSTEM_PROMPT_SUPPORT_FILES]
    return files


def remote_repo_dir(remote_root: str, repo_path: Path) -> str:
    """Return the remote directory path for a repository-relative path."""

    path_text = repo_path.as_posix()
    if path_text == ".":
        return remote_root
    return f"{remote_root}/{path_text}"


def batch_files(batch_dir: Path, assignment: HostAssignment) -> List[Path]:
    """Return batch metadata files needed on one host."""

    return [
        Path(repo_rel(batch_dir / "all-workflows.txt")),
        Path(repo_rel(batch_dir / "host-assignments.json")),
        Path(repo_rel(batch_dir / "shard-summary.json")),
        Path(repo_rel(batch_dir / assignment.shard_rel)),
    ]


def workflow_files_for_assignment(batch_dir: Path, assignment: HostAssignment) -> List[Path]:
    """Return repository-relative workflow YAMLs assigned to one host."""

    shard_path = batch_dir / assignment.shard_rel
    files: List[Path] = []
    for workflow_rel in workflow_list(shard_path):
        files.append(Path(repo_rel(resolve_workflow_file(workflow_rel))))
    return files


def sync_host(
    assignment: HostAssignment,
    *,
    batch_dir: Path,
    env_file: Path,
    key_path: Path,
    remote_root: str,
    remote_user: str,
) -> Dict[str, Any]:
    """Copy support files, env, metadata, and assigned workflows to one host."""

    remote_batch_root = f"{remote_root}/{batch_rel(batch_dir)}"
    host_batch_root = f"{remote_batch_root}/hosts/{assignment.safe_host}"
    files = campaign_support_files()
    files += batch_files(batch_dir, assignment)
    files += workflow_files_for_assignment(batch_dir, assignment)

    mkdirs = {
        remote_batch_root,
        f"{remote_batch_root}/shards",
        f"{remote_batch_root}/hosts",
        host_batch_root,
        f"{host_batch_root}/logs",
    }
    mkdirs.update(remote_repo_dir(remote_root, path.parent) for path in files)
    ssh(
        assignment.host,
        "mkdir -p " + " ".join(shlex.quote(item) for item in sorted(mkdirs)),
        key_path=key_path,
        remote_user=remote_user,
    )

    grouped = group_paths(files)
    for parent, entries in grouped.items():
        local_sources = [str((REPO_ROOT / item).resolve()) for item in sorted(entries)]
        remote_target = f"{remote_user}@{assignment.host}:{remote_repo_dir(remote_root, parent)}/"
        run(scp_base(key_path) + local_sources + [remote_target], check=True)

    run(
        scp_base(key_path)
        + [str(env_file.resolve()), f"{remote_user}@{assignment.host}:{remote_root}/{DEFAULT_REMOTE_ENV}"],
        check=True,
    )
    ssh(
        assignment.host,
        f"chmod 600 {shlex.quote(remote_root + '/' + DEFAULT_REMOTE_ENV)} && "
        f"chmod +x {shlex.quote(remote_root + '/' + repo_rel(QUEUE_RUNNER))}",
        key_path=key_path,
        remote_user=remote_user,
    )
    return {
        "host": assignment.host,
        "safe_host": assignment.safe_host,
        "workflow_count": len(workflow_list(batch_dir / assignment.shard_rel)),
        "host_batch_dir": f"{batch_rel(batch_dir)}/hosts/{assignment.safe_host}",
    }


def preflight_host(
    assignment: HostAssignment,
    *,
    batch_dir: Path,
    key_path: Path,
    remote_root: str,
    remote_user: str,
    remote_python: str,
    remote_env_file: str,
    cluster_name: str,
    kubeconfig_path: str,
    cleanup_timeout_sec: int,
    model: str,
) -> Dict[str, Any]:
    """Verify one host's model access, cluster baseline, and image prerequisites."""

    profiles = assignment.resolved_profiles(
        default_cluster_name=cluster_name,
        default_kubeconfig_path=kubeconfig_path,
    )
    remote_script = f"""
import json
import subprocess
import time
from pathlib import Path

repo = Path({remote_root!r})
remote_python = repo / {remote_python!r}
env_file = repo / {remote_env_file!r}
cleanup_timeout = int({cleanup_timeout_sec})
model = {model!r}
profiles = json.loads({json.dumps(profiles)!r})
protected = {sorted(['default','kube-system','kube-public','kube-node-lease','local-path-storage'])!r}

def run(cmd, shell=False, timeout=None):
    try:
        return subprocess.run(
            cmd,
            shell=shell,
            text=True,
            capture_output=True,
            cwd=str(repo),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        timeout_note = f"[timed out after {{int(timeout or 0)}}s]"
        if stderr:
            stderr = stderr.rstrip() + "\\n" + timeout_note
        else:
            stderr = timeout_note
        return subprocess.CompletedProcess(exc.cmd, 124, stdout, stderr)

def ok(cmd):
    return run(cmd, shell=True, timeout=30).returncode == 0

def extra_namespaces(kubeconfig_path):
    proc = run(["kubectl", "--kubeconfig", str(kubeconfig_path), "get", "ns", "-o", "json"], timeout=60)
    if proc.returncode != 0:
        return None, proc.stderr.strip() or proc.stdout.strip()
    payload = json.loads(proc.stdout or "{{}}")
    names = sorted(
        item.get("metadata", {{}}).get("name", "")
        for item in (payload.get("items") or [])
        if item.get("metadata", {{}}).get("name", "")
    )
    return [name for name in names if name not in protected], ""

result = {{
    "repo_ok": (repo / "orchestrator.py").exists(),
    "python_ok": remote_python.exists(),
    "docker_ok": ok("command -v docker"),
    "kind_ok": ok("command -v kind"),
    "kubectl_ok": ok("command -v kubectl"),
    "env_file_ok": env_file.exists(),
    "profile_count": len(profiles),
    "cluster_name": profiles[0]["cluster_name"] if len(profiles) == 1 else "",
    "cluster_names": [profile["cluster_name"] for profile in profiles],
    "cluster_exists": False,
    "kubeconfig_path": profiles[0]["kubeconfig_path"] if len(profiles) == 1 else "",
    "kubeconfig_paths": [profile["kubeconfig_path"] for profile in profiles],
    "kubeconfig_ready": False,
    "nodes_ready": False,
    "node_names": [],
    "profile_results": [],
    "namespace_cleanup": {{"profiles": [], "timed_out": False}},
    "image_present": False,
    "model": model,
    "model_available": False,
    "model_probe_stdout": "",
    "model_probe_stderr": "",
}}

clusters = run(["kind", "get", "clusters"], timeout=60)
known_clusters = {{
    line.strip()
    for line in (clusters.stdout or "").splitlines()
    if line.strip()
}} if clusters.returncode == 0 else set()

for profile in profiles:
    cluster_name = str(profile.get("cluster_name") or "")
    kubeconfig_path = Path(str(profile.get("kubeconfig_path") or ""))
    profile_result = {{
        "cluster_name": cluster_name,
        "kubeconfig_path": str(kubeconfig_path),
        "cluster_exists": False,
        "kubeconfig_ready": False,
        "nodes_ready": False,
        "node_names": [],
        "namespace_cleanup": {{}},
    }}
    if cluster_name:
        profile_result["cluster_exists"] = cluster_name in known_clusters
        if profile_result["cluster_exists"]:
            export = run(["kind", "export", "kubeconfig", "--name", cluster_name, "--kubeconfig", str(kubeconfig_path)], timeout=60)
            if export.returncode == 0:
                profile_result["kubeconfig_ready"] = True
    else:
        profile_result["cluster_exists"] = kubeconfig_path.exists()
        profile_result["kubeconfig_ready"] = kubeconfig_path.exists()
    if profile_result["kubeconfig_ready"]:
        nodes = run(["kubectl", "--kubeconfig", str(kubeconfig_path), "get", "nodes", "-o", "json"], timeout=60)
        if nodes.returncode == 0:
            payload = json.loads(nodes.stdout or "{{}}")
            items = payload.get("items") or []
            profile_result["node_names"] = [item.get("metadata", {{}}).get("name", "") for item in items]
            profile_result["nodes_ready"] = bool(items) and all(
                any(cond.get("type") == "Ready" and cond.get("status") == "True" for cond in (item.get("status", {{}}).get("conditions") or []))
                for item in items
            )
        before, err = extra_namespaces(kubeconfig_path)
        if before is None:
            profile_result["namespace_cleanup"] = {{"before": [], "remaining": [], "timed_out": True, "error": err}}
        else:
            for namespace in before:
                run(["kubectl", "--kubeconfig", str(kubeconfig_path), "delete", "namespace", namespace, "--wait=false"], timeout=30)
            deadline = time.monotonic() + cleanup_timeout
            remaining = before
            while time.monotonic() < deadline:
                remaining, err = extra_namespaces(kubeconfig_path)
                if remaining is None:
                    break
                if not remaining:
                    break
                time.sleep(2.0)
            profile_result["namespace_cleanup"] = {{
                "before": before,
                "remaining": remaining if isinstance(remaining, list) else before,
                "timed_out": bool(remaining),
            }}
    result["profile_results"].append(profile_result)

if result["profile_results"]:
    result["cluster_exists"] = all(item.get("cluster_exists") for item in result["profile_results"])
    result["kubeconfig_ready"] = all(item.get("kubeconfig_ready") for item in result["profile_results"])
    result["nodes_ready"] = all(item.get("nodes_ready") for item in result["profile_results"])
    result["node_names"] = sorted({{
        name
        for item in result["profile_results"]
        for name in (item.get("node_names") or [])
        if name
    }})
    result["namespace_cleanup"] = {{
        "profiles": [
            {{
                "cluster_name": item.get("cluster_name"),
                "kubeconfig_path": item.get("kubeconfig_path"),
                **(item.get("namespace_cleanup") or {{}})
            }}
            for item in result["profile_results"]
        ],
        "timed_out": any((item.get("namespace_cleanup") or {{}}).get("timed_out") for item in result["profile_results"]),
    }}

image = run(["docker", "image", "inspect", "karma-agent-copilot:latest"], timeout=60)
result["image_present"] = image.returncode == 0
if not result["image_present"]:
    build = run([
        "docker", "build", "-t", "karma-agent-copilot:latest",
        "-f", "karma/agents/copilot/Dockerfile",
        "karma/agents/copilot",
    ], timeout=1800)
    result["image_present"] = build.returncode == 0
    if build.returncode != 0:
        result["model_probe_stderr"] = build.stderr.strip() or build.stdout.strip()

if result["env_file_ok"] and result["image_present"]:
    probe_cmd = [
        "docker", "run", "--rm", "--entrypoint", "copilot",
        "--env-file", str(env_file),
        "karma-agent-copilot:latest",
        "-p", "Reply with exactly OK",
        "--allow-all",
        "-s",
    ]
    if model:
        probe_cmd += ["--model", model]
    probe = run(probe_cmd, timeout=180)
    result["model_probe_stdout"] = (probe.stdout or "").strip()
    result["model_probe_stderr"] = (probe.stderr or "").strip()
    result["model_available"] = probe.returncode == 0 and result["model_probe_stdout"] == "OK"

print(json.dumps(result))
"""
    proc = ssh(
        assignment.host,
        f"python3 - <<'PY'\n{remote_script}\nPY",
        key_path=key_path,
        remote_user=remote_user,
        check=False,
    )
    if proc.returncode != 0:
        return {
            "host": assignment.host,
            "safe_host": assignment.safe_host,
            "repo_ok": False,
            "python_ok": False,
            "docker_ok": False,
            "kind_ok": False,
            "kubectl_ok": False,
            "env_file_ok": False,
            "profile_count": len(profiles),
            "cluster_name": profiles[0]["cluster_name"] if len(profiles) == 1 else "",
            "cluster_names": [profile["cluster_name"] for profile in profiles],
            "cluster_exists": False,
            "kubeconfig_path": profiles[0]["kubeconfig_path"] if len(profiles) == 1 else "",
            "kubeconfig_paths": [profile["kubeconfig_path"] for profile in profiles],
            "kubeconfig_ready": False,
            "nodes_ready": False,
            "node_names": [],
            "profile_results": [],
            "namespace_cleanup": {"profiles": [], "timed_out": True},
            "image_present": False,
            "model": model,
            "model_available": False,
            "model_probe_stdout": "",
            "model_probe_stderr": "",
            "ssh_error": (proc.stderr or proc.stdout or "").strip(),
        }
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "host": assignment.host,
            "safe_host": assignment.safe_host,
            "repo_ok": False,
            "python_ok": False,
            "docker_ok": False,
            "kind_ok": False,
            "kubectl_ok": False,
            "env_file_ok": False,
            "profile_count": len(profiles),
            "cluster_name": profiles[0]["cluster_name"] if len(profiles) == 1 else "",
            "cluster_names": [profile["cluster_name"] for profile in profiles],
            "cluster_exists": False,
            "kubeconfig_path": profiles[0]["kubeconfig_path"] if len(profiles) == 1 else "",
            "kubeconfig_paths": [profile["kubeconfig_path"] for profile in profiles],
            "kubeconfig_ready": False,
            "nodes_ready": False,
            "node_names": [],
            "profile_results": [],
            "namespace_cleanup": {"profiles": [], "timed_out": True},
            "image_present": False,
            "model": model,
            "model_available": False,
            "model_probe_stdout": "",
            "model_probe_stderr": "",
            "ssh_error": (proc.stdout or "").strip(),
        }
    payload["host"] = assignment.host
    payload["safe_host"] = assignment.safe_host
    return payload


def status_host(
    assignment: HostAssignment,
    *,
    batch_dir: Path,
    key_path: Path,
    remote_root: str,
    remote_user: str,
) -> Dict[str, Any]:
    """Read one host's queue summary and running state."""

    host_batch_rel = f"{batch_rel(batch_dir)}/hosts/{assignment.safe_host}"
    remote_script = f"""
import json
import subprocess
from pathlib import Path
batch = Path({remote_root!r}) / {host_batch_rel!r}
status_path = batch / "status.json"
summary_path = batch / "summary.json"
launch_log_path = batch / "launch.log"
proc = subprocess.run(
    ["ps", "-eo", "pid=,command="],
    text=True,
    capture_output=True,
)
matches = []
if proc.returncode == 0:
    for line in (proc.stdout or "").splitlines():
        if "run_workflow_queue.py" in line and {host_batch_rel!r} in line:
            matches.append(line.strip())
payload = {{
    "status_exists": status_path.exists(),
    "summary_exists": summary_path.exists(),
    "host_batch_dir": str(batch),
    "running": bool(matches),
    "runner_matches": matches,
}}
if status_path.exists():
    payload["status"] = json.loads(status_path.read_text())
if summary_path.exists():
    payload["summary"] = json.loads(summary_path.read_text())
if launch_log_path.exists():
    payload["launch_log_tail"] = launch_log_path.read_text(errors="replace").splitlines()[-5:]
print(json.dumps(payload))
"""
    proc = ssh(
        assignment.host,
        f"python3 - <<'PY'\n{remote_script}\nPY",
        key_path=key_path,
        remote_user=remote_user,
    )
    payload = json.loads(proc.stdout)
    payload["host"] = assignment.host
    payload["safe_host"] = assignment.safe_host
    return payload


def prepare_batch(args: argparse.Namespace) -> int:
    """Evenly shard workflows across the host list."""

    batch_dir = Path(args.batch_dir).resolve()
    workflows = workflow_list(Path(args.workflow_list).resolve())
    hosts = host_specs(Path(args.hosts_json).resolve())
    if not workflows:
        raise SystemExit("workflow list is empty")
    if not hosts:
        raise SystemExit("host list is empty")
    shards_dir = batch_dir / "shards"
    shards_dir.mkdir(parents=True, exist_ok=True)

    profile_slots: List[str] = []
    for profile_index in range(max(host.profile_count for host in hosts)):
        for host in hosts:
            if profile_index < host.profile_count:
                profile_slots.append(host.host)
    assignments: Dict[str, List[str]] = {host.host: [] for host in hosts}
    for index, workflow_rel in enumerate(workflows):
        assignments[profile_slots[index % len(profile_slots)]].append(workflow_rel)

    host_payload: Dict[str, Any] = {}
    summary: Dict[str, Any] = {
        "generated_at": now_utc_iso(),
        "workflow_total": len(workflows),
        "hosts_total": len(hosts),
        "profile_total": len(profile_slots),
        "shards": [],
    }
    for index, host in enumerate(hosts, start=1):
        shard_rel = f"shards/shard-{index:02d}.txt"
        shard_path = batch_dir / shard_rel
        shard_path.write_text("".join(f"{item}\n" for item in assignments[host.host]))
        host_payload[host.host] = {
            "shard": shard_rel,
            "kubeconfigs": list(host.kubeconfigs),
            "cluster_names": list(host.cluster_names),
        }
        summary["shards"].append(
            {
                "host": host.host,
                "shard": shard_rel,
                "workflow_count": len(assignments[host.host]),
                "profile_count": host.profile_count,
                "kubeconfigs": list(host.kubeconfigs),
                "cluster_names": list(host.cluster_names),
                "workflows": assignments[host.host],
            }
        )

    (batch_dir / "all-workflows.txt").write_text("".join(f"{item}\n" for item in workflows))
    write_json(batch_dir / "host-assignments.json", host_payload)
    write_json(batch_dir / "shard-summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


def sync_batch(args: argparse.Namespace) -> int:
    """Sync campaign inputs and the env file to every host."""

    batch_dir = Path(args.batch_dir).resolve()
    env_file = Path(args.env_file).resolve()
    key_path = Path(args.ssh_key).expanduser()
    results = [
        sync_host(
            assignment,
            batch_dir=batch_dir,
            env_file=env_file,
            key_path=key_path,
            remote_root=args.remote_root,
            remote_user=args.remote_user,
        )
        for assignment in load_assignments(batch_dir)
    ]
    print(json.dumps({"generated_at": now_utc_iso(), "results": results}, indent=2))
    return 0


def preflight_batch(args: argparse.Namespace) -> int:
    """Probe model availability and cluster baseline on every host."""

    batch_dir = Path(args.batch_dir).resolve()
    key_path = Path(args.ssh_key).expanduser()
    rows = [
        preflight_host(
            assignment,
            batch_dir=batch_dir,
            key_path=key_path,
            remote_root=args.remote_root,
            remote_user=args.remote_user,
            remote_python=args.remote_python,
            remote_env_file=args.remote_env_file,
            cluster_name=args.cluster_name,
            kubeconfig_path=args.kubeconfig_path,
            cleanup_timeout_sec=args.namespace_cleanup_timeout,
            model=args.copilot_model,
        )
        for assignment in load_assignments(batch_dir)
    ]
    payload = {"generated_at": now_utc_iso(), "results": rows}
    print(json.dumps(payload, indent=2))
    ok = all(
        row.get("repo_ok")
        and row.get("python_ok")
        and row.get("docker_ok")
        and row.get("kind_ok")
        and row.get("kubectl_ok")
        and row.get("env_file_ok")
        and row.get("cluster_exists")
        and row.get("kubeconfig_ready")
        and row.get("nodes_ready")
        and not row.get("namespace_cleanup", {}).get("timed_out")
        and row.get("image_present")
        and row.get("model_available")
        for row in rows
    )
    return 0 if ok else 1


def launch_batch(args: argparse.Namespace) -> int:
    """Launch one queue-runner process per host."""

    batch_dir = Path(args.batch_dir).resolve()
    key_path = Path(args.ssh_key).expanduser()
    batch_rel_value = batch_rel(batch_dir)
    remote_python_abs = f"{args.remote_root}/{args.remote_python}".replace("//", "/")
    results = []
    for assignment in load_assignments(batch_dir):
        host_batch_rel = f"{batch_rel_value}/hosts/{assignment.safe_host}"
        host_batch_abs = f"{args.remote_root}/{host_batch_rel}"
        shard_rel = f"{batch_rel_value}/{assignment.shard_rel}"
        runs_dir = f"runs/{args.runs_subdir}/{assignment.safe_host}"
        model_arg = f" --copilot-model {shlex.quote(args.copilot_model)}" if args.copilot_model else ""
        kubeconfigs_arg = ",".join(assignment.kubeconfigs) or args.kubeconfig_path
        remote_cmd = (
            "set -euo pipefail; "
            f"cd {shlex.quote(args.remote_root)}; "
            f"mkdir -p {shlex.quote(host_batch_abs)} {shlex.quote(host_batch_abs + '/logs')}; "
            f"nohup bash -lc {shlex.quote(f'cd {args.remote_root} && exec python3 scripts/remote-agents/run_workflow_queue.py --workflow-list {shard_rel} --kubeconfigs {kubeconfigs_arg} --batch-dir {host_batch_rel} --runtime-python {remote_python_abs} --agent copilot --sandbox docker --runs-dir {runs_dir} --llm-env-file {args.remote_env_file} --resume --max-heavy {args.max_heavy}{model_arg} --namespace-cleanup-timeout {args.namespace_cleanup_timeout}')} "
            f"> {shlex.quote(host_batch_abs + '/launch.log')} 2>&1 < /dev/null & printf '%s\\n' \"$!\""
        )
        proc = ssh(
            assignment.host,
            remote_cmd,
            key_path=key_path,
            remote_user=args.remote_user,
        )
        results.append(
            {
                "host": assignment.host,
                "safe_host": assignment.safe_host,
                "pid": (proc.stdout or "").strip(),
                "host_batch_dir": host_batch_rel,
                "workflow_count": len(workflow_list(batch_dir / assignment.shard_rel)),
            }
        )
        time.sleep(args.launch_settle_sec)
    print(json.dumps({"generated_at": now_utc_iso(), "results": results}, indent=2))
    return 0


def status_batch(args: argparse.Namespace) -> int:
    """Aggregate queue status across the campaign hosts."""

    batch_dir = Path(args.batch_dir).resolve()
    key_path = Path(args.ssh_key).expanduser()
    rows = [
        status_host(
            assignment,
            batch_dir=batch_dir,
            key_path=key_path,
            remote_root=args.remote_root,
            remote_user=args.remote_user,
        )
        for assignment in load_assignments(batch_dir)
    ]
    totals: Dict[str, int] = {}
    completed = 0
    remaining = 0
    inflight = 0
    for row in rows:
        status_payload = row.get("status") or {}
        completed += int(status_payload.get("completed") or 0)
        remaining += int(status_payload.get("remaining") or 0)
        inflight += int(status_payload.get("inflight") or 0)
        for key, value in (status_payload.get("outcome_counts") or {}).items():
            totals[key] = totals.get(key, 0) + int(value)
    payload = {
        "generated_at": now_utc_iso(),
        "batch_dir": str(batch_dir),
        "completed": completed,
        "remaining": remaining,
        "inflight": inflight,
        "outcome_counts": dict(sorted(totals.items())),
        "hosts": rows,
    }
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser."""

    parser = argparse.ArgumentParser(description="Manage distributed Copilot workflow campaigns.")
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="Evenly shard workflows across hosts.")
    prepare.add_argument("--batch-dir", required=True)
    prepare.add_argument("--workflow-list", required=True)
    prepare.add_argument("--hosts-json", required=True)
    prepare.set_defaults(func=prepare_batch)

    for name, help_text, func in (
        ("sync", "Sync env file, queue runner, metadata, and assigned workflows.", sync_batch),
        ("preflight", "Check model access and cluster baseline on every host.", preflight_batch),
        ("launch", "Launch one queue-runner process per host.", launch_batch),
        ("status", "Read aggregate campaign status across hosts.", status_batch),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--batch-dir", required=True)
        p.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
        p.add_argument("--remote-user", default=DEFAULT_REMOTE_USER)
        p.add_argument("--remote-python", default=DEFAULT_REMOTE_PYTHON)
        p.add_argument("--ssh-key", default=str(DEFAULT_SSH_KEY))
        if name == "sync":
            p.add_argument("--env-file", required=True)
        if name in {"preflight", "launch"}:
            p.add_argument("--copilot-model", default="gpt-5.3-codex")
            p.add_argument("--remote-env-file", default=DEFAULT_REMOTE_ENV)
            p.add_argument("--cluster-name", default=DEFAULT_CLUSTER_NAME)
            p.add_argument("--kubeconfig-path", default=DEFAULT_REMOTE_KUBECONFIG)
            p.add_argument("--namespace-cleanup-timeout", type=int, default=240)
        if name == "launch":
            p.add_argument("--runs-subdir", default="copilot-campaign")
            p.add_argument("--max-heavy", type=int, default=1)
            p.add_argument("--launch-settle-sec", type=float, default=2.0)
        p.set_defaults(func=func)

    return parser


def main() -> int:
    """CLI entrypoint."""

    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
