#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
from pathlib import Path

from _shared import PLANS_ROOT, REGISTRY_DIR, STATIC_ROOT, read_yaml


def main() -> int:
    payload = read_yaml(REGISTRY_DIR / "workflow_support.yaml")
    workflows = payload.get("workflows") or []
    counts = Counter(item.get("status") or "unknown" for item in workflows)

    missing_plans: list[str] = []
    missing_solvers: list[str] = []
    for workflow in workflows:
        if workflow.get("status") != "candidate":
            continue
        plan_rel = workflow.get("plan_path") or ""
        plan_path = STATIC_ROOT / plan_rel
        if not plan_rel or not plan_path.exists():
            missing_plans.append(str(workflow.get("workflow") or ""))
        for stage in workflow.get("stages") or []:
            solver_rel = stage.get("solver_rel_path") or ""
            if not solver_rel:
                missing_solvers.append(
                    f'{workflow.get("workflow")}: {stage.get("stage_id")} (missing solver path)'
                )
                continue
            solver_path = STATIC_ROOT / "solvers" / solver_rel
            if not solver_path.exists():
                missing_solvers.append(
                    f'{workflow.get("workflow")}: {stage.get("stage_id")} -> {solver_rel}'
                )

    print("workflow support summary")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")
    print(f"  plans_root: {PLANS_ROOT}")
    print(f"  missing_candidate_plans: {len(missing_plans)}")
    print(f"  missing_candidate_solvers: {len(missing_solvers)}")

    if missing_plans:
        print("missing plan files:")
        for item in missing_plans[:50]:
            print(f"  - {item}")
    if missing_solvers:
        print("missing solver files:")
        for item in missing_solvers[:50]:
            print(f"  - {item}")

    return 1 if missing_plans or missing_solvers else 0


if __name__ == "__main__":
    raise SystemExit(main())
