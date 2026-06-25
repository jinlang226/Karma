#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from _shared import (
    GENERATED_MANIFESTS_DIR,
    PLANS_ROOT,
    REGISTRY_DIR,
    STATIC_ROOT,
    WORKFLOWS_DIR,
    ensure_parent,
    now_utc_iso,
    read_yaml,
    workflow_plan_path,
    write_json,
    write_yaml,
)


SOLVERS_ROOT = STATIC_ROOT / "solvers"


def _load_case_map() -> dict[tuple[str, str], dict]:
    payload = read_yaml(REGISTRY_DIR / "current_case_map.yaml")
    out: dict[tuple[str, str], dict] = {}
    for record in payload.get("cases") or []:
        out[(record["service"], record["case_name"])] = record
    return out


def _stage_rows(workflow_path: Path) -> list[dict]:
    data = read_yaml(workflow_path)
    spec = data.get("spec") or {}
    return list(spec.get("stages") or [])


def _solver_rel_path(service: str, case_name: str) -> str:
    return f"{service}/{case_name}.sh"


def _submit_message(service: str, case_name: str) -> str:
    return f"submitted static solver for {service}/{case_name}"


def _solver_script_body(record: dict) -> str:
    service = record["service"]
    case_name = record["case_name"]
    strategy = record["strategy"]
    imported_case = record.get("imported_case") or ""
    solver_path = record.get("solver_path") or ""
    notes = record.get("notes") or ""
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        '# shellcheck source=../../lib/common.sh',
        'source "${SCRIPT_DIR}/../../lib/common.sh"',
        "",
        f"# Current case: {service}/{case_name}",
        f"# Strategy: {strategy}",
    ]
    if imported_case:
        lines.append(f"# Imported reference: {imported_case}")
    if solver_path:
        lines.append(f"# Vendored solver: {solver_path}")
    if notes:
        lines.append(f"# Notes: {notes}")
    lines.append("")

    if strategy == "submit_only_candidate":
        lines.append(f'static_solver_submit_only "{_submit_message(service, case_name)}"')
    elif strategy == "python_wrapper":
        lines.append(
            f'static_solver_run_vendored_resource_python "{imported_case}" "{_submit_message(service, case_name)}"'
        )
    elif strategy in {"direct_shell", "shell_wrapper_variant"}:
        lines.append(f'static_solver_run_vendored_shell "{solver_path}"')
    else:
        raise RuntimeError(f"unsupported strategy for active solver generation: {strategy}")

    lines.append("")
    return "\n".join(lines)


def _write_solver_scripts(case_map: dict[tuple[str, str], dict]) -> list[str]:
    written: list[str] = []
    for record in sorted(case_map.values(), key=lambda item: (item["service"], item["case_name"])):
        if record["status"] not in {"candidate", "review_required"}:
            continue
        solver_path = SOLVERS_ROOT / record["service"] / f"{record['case_name']}.sh"
        ensure_parent(solver_path)
        solver_path.write_text(_solver_script_body(record))
        solver_path.chmod(0o755)
        written.append(solver_path.relative_to(STATIC_ROOT).as_posix())
    return written


def _workflow_status(stage_records: list[dict]) -> tuple[str, list[str]]:
    statuses = [stage["case_status"] for stage in stage_records]
    reasons: list[str] = []
    if any(status == "unsupported" for status in statuses):
        reasons.append("contains unsupported case")
        return "unsupported", reasons
    if any(status == "review_required" for status in statuses):
        reasons.append("contains review-required case")
        return "review_required", reasons
    return "candidate", reasons


def _write_plan_file(workflow_path: Path, stage_records: list[dict]) -> str:
    plan_path = workflow_plan_path(workflow_path)
    lines = [
        "#!/usr/bin/env bash",
        f"# Generated from workflows/{workflow_path.relative_to(WORKFLOWS_DIR).as_posix()}",
        "",
    ]
    for stage in stage_records:
        lines.append(f'plan_stage "{stage["stage_id"]}" "{stage["solver_rel_path"]}"')
    lines.append("")
    ensure_parent(plan_path)
    plan_path.write_text("\n".join(lines))
    plan_path.chmod(0o755)
    return plan_path.relative_to(STATIC_ROOT).as_posix()


def main() -> int:
    case_map = _load_case_map()
    solver_scripts = _write_solver_scripts(case_map)

    workflows_payload: list[dict] = []
    candidate_workflows: list[str] = []
    review_required_workflows: list[str] = []
    unsupported_workflows: list[str] = []
    candidate_plan_paths: list[str] = []

    for workflow_path in sorted(WORKFLOWS_DIR.rglob("*.yaml")):
        stage_records: list[dict] = []
        for stage in _stage_rows(workflow_path):
            service = str(stage.get("service") or "")
            case_name = str(stage.get("case") or "")
            record = case_map.get((service, case_name))
            if record is None:
                stage_records.append(
                    {
                        "stage_id": str(stage.get("id") or ""),
                        "service": service,
                        "case_name": case_name,
                        "case_status": "unsupported",
                        "solver_rel_path": "",
                        "reason": "missing case registry entry",
                    }
                )
                continue
            stage_records.append(
                {
                    "stage_id": str(stage.get("id") or ""),
                    "service": service,
                    "case_name": case_name,
                    "case_status": record["status"],
                    "solver_rel_path": _solver_rel_path(service, case_name),
                    "strategy": record["strategy"],
                    "imported_case": record.get("imported_case") or "",
                    "notes": record.get("notes") or "",
                }
            )

        status, reasons = _workflow_status(stage_records)
        plan_rel_path = ""
        rel_workflow = workflow_path.relative_to(WORKFLOWS_DIR).as_posix()
        if status == "candidate":
            plan_rel_path = _write_plan_file(workflow_path, stage_records)
            candidate_workflows.append(rel_workflow)
            candidate_plan_paths.append(plan_rel_path)
        elif status == "review_required":
            review_required_workflows.append(rel_workflow)
        else:
            unsupported_workflows.append(rel_workflow)

        workflows_payload.append(
            {
                "workflow": rel_workflow,
                "status": status,
                "reasons": reasons,
                "plan_path": plan_rel_path,
                "stages": stage_records,
            }
        )

    workflow_support_payload = {
        "version": 1,
        "generated_at": now_utc_iso(),
        "workflows": workflows_payload,
    }
    write_yaml(REGISTRY_DIR / "workflow_support.yaml", workflow_support_payload)
    write_json(
        GENERATED_MANIFESTS_DIR / "candidate_workflows.json",
        {
            "generated_at": now_utc_iso(),
            "candidate_workflows": candidate_workflows,
            "review_required_workflows": review_required_workflows,
            "unsupported_workflows": unsupported_workflows,
            "generated_solver_scripts": solver_scripts,
            "generated_plan_paths": candidate_plan_paths,
        },
    )
    (GENERATED_MANIFESTS_DIR / "supported_workflows.txt").write_text(
        "".join(f"{item}\n" for item in candidate_workflows)
    )
    (GENERATED_MANIFESTS_DIR / "skipped_workflows.txt").write_text(
        "".join(
            [f"review_required\t{item}\n" for item in review_required_workflows]
            + [f"unsupported\t{item}\n" for item in unsupported_workflows]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
