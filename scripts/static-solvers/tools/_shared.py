from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
STATIC_ROOT = REPO_ROOT / "scripts" / "static-solvers"
WORKFLOWS_DIR = REPO_ROOT / "workflows"
CASES_DIR = REPO_ROOT / "cases"
VENDOR_BRANCH = "import-improve-resources"
VENDOR_ROOT = STATIC_ROOT / "vendor" / VENDOR_BRANCH
PLANS_ROOT = STATIC_ROOT / "plans" / "workflows"
REGISTRY_DIR = STATIC_ROOT / "registry"
GENERATED_MANIFESTS_DIR = STATIC_ROOT / "generated" / "manifests"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text()) or {}


def write_yaml(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd or REPO_ROOT),
        text=True,
        capture_output=True,
        check=check,
    )


def git_show(ref: str, repo_path: str) -> str:
    return run(["git", "show", f"{ref}:{repo_path}"]).stdout


def git_ls_tree(ref: str, prefix: str = "") -> list[str]:
    proc = run(["git", "ls-tree", "-r", "--name-only", ref], check=True)
    items = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if prefix:
        items = [item for item in items if item.startswith(prefix)]
    return items


def relative_workflow_path(workflow_path: Path) -> str:
    return workflow_path.relative_to(WORKFLOWS_DIR).as_posix()


def workflow_key(workflow_path: Path) -> str:
    return workflow_path.relative_to(WORKFLOWS_DIR).with_suffix("").as_posix()


def workflow_plan_path(workflow_path: Path) -> Path:
    return PLANS_ROOT / workflow_path.relative_to(WORKFLOWS_DIR).with_suffix(".sh")


def slugify_function_name(service: str, case_name: str) -> str:
    raw = f"solve_{service}_{case_name}"
    out = []
    for ch in raw:
        if ch.isalnum():
            out.append(ch.lower())
        else:
            out.append("_")
    collapsed = "".join(out)
    while "__" in collapsed:
        collapsed = collapsed.replace("__", "_")
    return collapsed.strip("_")

