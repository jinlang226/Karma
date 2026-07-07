#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from _shared import STATIC_ROOT, ensure_parent, now_utc_iso, write_json


VALIDATION_DIR = STATIC_ROOT / "generated" / "validation"
DEFAULT_RESULTS_PATH = VALIDATION_DIR / "workflow_validation_results.jsonl"
DEFAULT_REPORT_PATH = VALIDATION_DIR / "env_runtime_retriage.json"
DEFAULT_LIKELY_RESOURCE_LIST = VALIDATION_DIR / "env_runtime_likely_resource.txt"
DEFAULT_RERUN_LIST = VALIDATION_DIR / "env_runtime_rerun_candidates.txt"


def _load_results(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _combined_text(record: dict) -> str:
    parts = [
        str(record.get("reason") or ""),
        str(record.get("stdout_tail") or ""),
        str(record.get("stderr_tail") or ""),
        str((record.get("failed_stage") or {}).get("error") or ""),
    ]
    return "\n".join(parts).lower()


def _classify_env_runtime(record: dict) -> tuple[str, str]:
    text = _combined_text(record)

    if (
        "connection refused" in text
        or "did you specify the right host or port" in text
        or "unable to connect to the server" in text
        or "failed to ensure namespace" in text
        or "failed to delete namespace" in text
        or "address already in use" in text
        or "kubectl proxy" in text
        or "failed to download openapi" in text
        or "tls handshake timeout" in text
        or "i/o timeout" in text
    ):
        return "likely_resource_runtime", "cluster/proxy/apiserver connectivity collapse"

    if "timed out waiting for the condition" in text:
        return "rerun_candidate", "condition timeout without connectivity-collapse signature"

    if "being terminated" in text or "object is being deleted" in text:
        return "rerun_candidate", "namespace/object termination race on first pass"

    return "rerun_candidate", "runtime issue without strong resource-collapse signature"


def _write_list(path: Path, workflows: list[str]) -> None:
    ensure_parent(path)
    path.write_text("".join(f"{item}\n" for item in workflows))


def main() -> int:
    results_path = DEFAULT_RESULTS_PATH
    report_path = DEFAULT_REPORT_PATH
    likely_resource_path = DEFAULT_LIKELY_RESOURCE_LIST
    rerun_path = DEFAULT_RERUN_LIST

    records = _load_results(results_path)
    env_runtime_records = [record for record in records if record.get("classification") == "env_runtime_issue"]

    reviewed: list[dict] = []
    counts = Counter()
    likely_resource_workflows: list[str] = []
    rerun_workflows: list[str] = []

    for record in env_runtime_records:
        bucket, note = _classify_env_runtime(record)
        counts[bucket] += 1
        workflow = str(record["workflow"])
        reviewed.append(
            {
                "workflow": workflow,
                "first_pass_reason": record.get("reason") or "",
                "bucket": bucket,
                "note": note,
                "failed_stage_id": str((record.get("failed_stage") or {}).get("stage_id") or ""),
                "run_dir": record.get("run_dir") or "",
            }
        )
        if bucket == "likely_resource_runtime":
            likely_resource_workflows.append(workflow)
        else:
            rerun_workflows.append(workflow)

    report = {
        "generated_at": now_utc_iso(),
        "source_results": str(results_path),
        "env_runtime_issue_total": len(env_runtime_records),
        "counts": dict(sorted(counts.items())),
        "likely_resource_workflows": likely_resource_workflows,
        "rerun_candidate_workflows": rerun_workflows,
        "records": reviewed,
    }
    write_json(report_path, report)
    _write_list(likely_resource_path, likely_resource_workflows)
    _write_list(rerun_path, rerun_workflows)

    print(json.dumps(
        {
            "report_path": str(report_path),
            "likely_resource_list": str(likely_resource_path),
            "rerun_candidate_list": str(rerun_path),
            "counts": dict(sorted(counts.items())),
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
