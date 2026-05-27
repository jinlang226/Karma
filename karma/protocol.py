"""
Run-directory layout, artifact paths, and file name contracts.

All modules that read or write run artifacts import path helpers from here.
No path strings are hardcoded elsewhere in the codebase.

Run directory layout::

    runs/
    └── {run_id}/
        ├── run.json
        ├── workflow_state.json
        ├── bundle/
        │   ├── kubeconfig
        │   └── env.json
        └── stages/
            └── {stage_id}/
                ├── stage.json
                ├── prompt.txt
                ├── submit.txt
                ├── oracle.json
                ├── evidence.json
                ├── kubectl_log.jsonl
                ├── precondition.log
                ├── adversary.log
                └── agent.log
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

_UNSAFE_RUN_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


# ---------------------------------------------------------------------------
# Run-level paths
# ---------------------------------------------------------------------------

def run_meta_path(run_dir: Path) -> Path:
    """Return the path to the top-level run metadata file."""
    return run_dir / "run.json"


def workflow_state_path(run_dir: Path) -> Path:
    """Return the path to the workflow state file updated after each stage."""
    return run_dir / "workflow_state.json"


def bundle_dir(run_dir: Path) -> Path:
    """Return the path to the agent credential bundle directory."""
    return run_dir / "bundle"


def bundle_kubeconfig_path(run_dir: Path) -> Path:
    """Return the path to the agent kubeconfig inside the bundle directory."""
    return bundle_dir(run_dir) / "kubeconfig"


def bundle_env_path(run_dir: Path) -> Path:
    """Return the path to the agent env vars JSON file inside the bundle directory."""
    return bundle_dir(run_dir) / "env.json"


# ---------------------------------------------------------------------------
# Stage-level paths
# ---------------------------------------------------------------------------

def stage_dir(run_dir: Path, stage_id: str) -> Path:
    """Return the path to the directory for a specific stage."""
    return run_dir / "stages" / stage_id


def stage_meta_path(run_dir: Path, stage_id: str) -> Path:
    """Return the path to the stage metadata and outcome file."""
    return stage_dir(run_dir, stage_id) / "stage.json"


def stage_prompt_path(run_dir: Path, stage_id: str) -> Path:
    """Return the path to the rendered agent prompt for this stage."""
    return stage_dir(run_dir, stage_id) / "prompt.txt"


def stage_submit_path(run_dir: Path, stage_id: str) -> Path:
    """Return the path to the agent's submitted answer for this stage."""
    return stage_dir(run_dir, stage_id) / "submit.txt"


def stage_oracle_path(run_dir: Path, stage_id: str) -> Path:
    """Return the path to the oracle verdict JSON for this stage."""
    return stage_dir(run_dir, stage_id) / "oracle.json"


def stage_evidence_path(run_dir: Path, stage_id: str) -> Path:
    """Return the path to the collected evidence JSON for this stage."""
    return stage_dir(run_dir, stage_id) / "evidence.json"


def stage_kubectl_log_path(run_dir: Path, stage_id: str) -> Path:
    """Return the path to the kubectl call log produced by the proxy."""
    return stage_dir(run_dir, stage_id) / "kubectl_log.jsonl"


def stage_precondition_log_path(run_dir: Path, stage_id: str) -> Path:
    """Return the path to the precondition setup command output log."""
    return stage_dir(run_dir, stage_id) / "precondition.log"


def stage_adversary_log_path(run_dir: Path, stage_id: str) -> Path:
    """Return the path to the adversary deploy and lift command output log."""
    return stage_dir(run_dir, stage_id) / "adversary.log"


def stage_agent_log_path(run_dir: Path, stage_id: str) -> Path:
    """Return the path to the agent stdout/stderr log for this stage."""
    return stage_dir(run_dir, stage_id) / "agent.log"


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def ensure_stage_dir(run_dir: Path, stage_id: str) -> Path:
    """Create the stage directory if it does not exist and return its path."""
    path = stage_dir(run_dir, stage_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_bundle_dir(run_dir: Path) -> Path:
    """Create the bundle directory if it does not exist and return its path."""
    path = bundle_dir(run_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_run_id(workflow_id: str, *, ts: str | None = None) -> str:
    """Return a run id derived from *workflow_id* and a UTC timestamp.

    The format is ``{workflow_id}-{YYYYMMDD_HHMMSS}``. If *ts* is provided
    it is used as the timestamp string verbatim.

    The *workflow_id* is sanitized so the run id is safe to use both as a
    single path segment and as a URL path variable. Any run of characters
    outside ``[A-Za-z0-9._-]`` (notably the ``/`` in single-case workflow
    ids like ``service/case``) is collapsed to a single hyphen.
    """
    if ts is None:
        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_id = _UNSAFE_RUN_ID_CHARS.sub("-", workflow_id).strip("-") or "workflow"
    return f"{safe_id}-{ts}"
