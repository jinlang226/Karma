import json
from pathlib import Path

from ..settings import ACTION_TRACE_LOG
from ..util import parse_ts


def _first_action_ts(trace_path, start_ts):
    if not trace_path or not trace_path.exists() or start_ts is None:
        return None
    try:
        with trace_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = record.get("event")
                if (
                    "status" not in record
                    and event != "connection_start"
                    and "command" not in record
                    and "args" not in record
                ):
                    continue
                ts = parse_ts(record.get("ts"))
                if ts is None:
                    continue
                if ts >= start_ts:
                    return ts
    except OSError:
        return None
    return None


def compute(meta, run_dir, trace_path=None):
    start_ts = parse_ts(meta.get("solve_started_at_ts") or meta.get("solve_started_at"))
    finish_ts = parse_ts(meta.get("finished_at_ts") or meta.get("finished_at"))
    status = meta.get("status")

    if trace_path is None:
        trace_path = ACTION_TRACE_LOG
    else:
        trace_path = Path(trace_path)

    first_action = _first_action_ts(trace_path, start_ts)
    time_to_first = None
    if start_ts and first_action:
        time_to_first = int((first_action - start_ts).total_seconds())

    time_to_success = None
    if start_ts and finish_ts and status == "passed":
        time_to_success = int((finish_ts - start_ts).total_seconds())

    return {
        "time_to_first_mutation_seconds": time_to_first,
        "time_to_success_seconds": time_to_success,
        "action_trace": str(trace_path) if trace_path else None,
    }
