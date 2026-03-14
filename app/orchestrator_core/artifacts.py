from __future__ import annotations

import json
import time
from pathlib import Path

from app.orchestrator_core.bundle import ingest_agent_usage as _bundle_ingest_agent_usage
from app.orchestrator_core.common import (
    read_json_file as _common_read_json_file,
    relative_path as _common_relative_path,
    write_json_file as _common_write_json_file,
)
from app.settings import ROOT


def write_submit_result(path, payload):
    try:
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True


def append_submit_result_log(run_dir, payload):
    try:
        with Path(run_dir, "submit_results.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
    except OSError:
        return False
    return True


def write_stage(run_dir, stage, detail=None, *, time_module=time, print_fn=print):
    payload = {"stage": stage, "ts": time_module.strftime("%Y-%m-%dT%H:%M:%SZ", time_module.gmtime())}
    if detail:
        payload["detail"] = detail
    try:
        Path(run_dir, "orchestrator_stage.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return False
    print_fn(f"[orchestrator] stage={stage}", flush=True)
    return True


def read_json_file(path):
    return _common_read_json_file(path)


def write_json_file(path, payload):
    return _common_write_json_file(path, payload)


def relative_path(path, *, root=ROOT):
    return _common_relative_path(path, root=root)


def ingest_agent_usage(run_dir, *, root=ROOT):
    return _bundle_ingest_agent_usage(
        run_dir,
        read_json_file=read_json_file,
        write_json_file=write_json_file,
        relative_path=lambda value: relative_path(value, root=root),
    )


def attach_agent_usage_fields(outcome, *, root=ROOT, ingest_agent_usage_fn=ingest_agent_usage):
    if not isinstance(outcome, dict):
        return outcome
    run_dir = outcome.get("run_dir")
    if not run_dir:
        return outcome
    run_path = Path(run_dir)
    if not run_path.is_absolute():
        run_path = (Path(root) / run_path).resolve()
    usage = ingest_agent_usage_fn(run_path, root=root)
    if not usage:
        return outcome
    merged = dict(outcome)
    merged.update(usage)
    return merged
