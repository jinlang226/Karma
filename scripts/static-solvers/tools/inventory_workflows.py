#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
from pathlib import Path

from _shared import CASES_DIR, GENERATED_MANIFESTS_DIR, WORKFLOWS_DIR, now_utc_iso, read_yaml, write_json


def _stage_rows(workflow_path: Path) -> list[dict]:
    data = read_yaml(workflow_path)
    spec = data.get("spec") or {}
    return list(spec.get("stages") or [])


def main() -> int:
    workflow_files = sorted(WORKFLOWS_DIR.rglob("*.yaml"))
    case_files = sorted(CASES_DIR.glob("*/*/test.yaml"))

    service_case_usage: Counter[tuple[str, str]] = Counter()
    workflow_records: list[dict] = []
    stage_count_distribution: Counter[int] = Counter()
    workflow_counts_by_dir: Counter[str] = Counter()

    for workflow_path in workflow_files:
        stages = _stage_rows(workflow_path)
        stage_count_distribution[len(stages)] += 1
        workflow_counts_by_dir[workflow_path.parent.relative_to(WORKFLOWS_DIR).as_posix() or "."] += 1
        stage_records = []
        for stage in stages:
            service = str(stage.get("service") or "")
            case_name = str(stage.get("case") or "")
            if service and case_name:
                service_case_usage[(service, case_name)] += 1
            stage_records.append(
                {
                    "id": str(stage.get("id") or ""),
                    "service": service,
                    "case_name": case_name,
                    "retries": int(stage.get("retries") or 0),
                }
            )
        workflow_records.append(
            {
                "path": workflow_path.relative_to(WORKFLOWS_DIR).as_posix(),
                "stage_count": len(stages),
                "stages": stage_records,
            }
        )

    inventory_payload = {
        "generated_at": now_utc_iso(),
        "workflow_count": len(workflow_files),
        "case_count": len(case_files),
        "unique_service_case_pairs": len(service_case_usage),
        "workflow_counts_by_dir": [
            {"directory": directory, "count": count}
            for directory, count in sorted(workflow_counts_by_dir.items())
        ],
        "stage_count_distribution": [
            {"stage_count": stage_count, "workflow_count": count}
            for stage_count, count in sorted(stage_count_distribution.items())
        ],
        "workflows": workflow_records,
    }
    case_usage_payload = {
        "generated_at": now_utc_iso(),
        "cases": [
            {"service": service, "case_name": case_name, "workflow_stage_count": count}
            for (service, case_name), count in sorted(
                service_case_usage.items(),
                key=lambda item: (-item[1], item[0][0], item[0][1]),
            )
        ],
    }

    write_json(GENERATED_MANIFESTS_DIR / "workflow_inventory.json", inventory_payload)
    write_json(GENERATED_MANIFESTS_DIR / "case_usage.json", case_usage_payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
