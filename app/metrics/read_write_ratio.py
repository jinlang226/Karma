import json
import shlex
from pathlib import Path

from ..settings import ACTION_TRACE_LOG


READ_VERBS = {"get", "describe", "logs", "log", "rollout_status", "rollout_history"}
WRITE_VERBS = {
    "apply",
    "patch",
    "delete",
    "create",
    "replace",
    "edit",
    "scale",
    "rollout",
}
EXEC_VERBS = {"exec"}


def _command_from_record(record):
    command = record.get("command")
    if command is None and "args" in record:
        command = record.get("args")
    if command is None:
        return None
    if isinstance(command, list):
        return " ".join(str(part) for part in command if part is not None)
    return str(command)


def _extract_verb(tokens):
    if not tokens or tokens[0] != "kubectl":
        return None
    for idx, token in enumerate(tokens[1:], start=1):
        if token == "rollout":
            next_token = None
            for lookahead in tokens[idx + 1 :]:
                if lookahead.startswith("-"):
                    continue
                next_token = lookahead
                break
            if next_token == "status":
                return "rollout_status"
            if next_token == "history":
                return "rollout_history"
            return "rollout"
        if token in READ_VERBS or token in WRITE_VERBS or token in EXEC_VERBS:
            return token
    return None


def compute(meta, run_dir, trace_path=None):
    if trace_path is None:
        trace_path = ACTION_TRACE_LOG
    else:
        trace_path = Path(trace_path)

    reads = 0
    writes = 0
    execs = 0
    others = 0
    total = 0
    command_counts = {}
    write_command_counts = {}

    if not trace_path or not trace_path.exists():
        return {"error": "action trace not found", "action_trace": str(trace_path)}

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
                cmd = _command_from_record(record)
                if not cmd:
                    continue
                total += 1
                normalized = " ".join(shlex.split(cmd))
                command_counts[normalized] = command_counts.get(normalized, 0) + 1
                try:
                    tokens = shlex.split(cmd)
                except ValueError:
                    tokens = cmd.split()
                verb = _extract_verb(tokens)
                if verb in READ_VERBS:
                    reads += 1
                elif verb in WRITE_VERBS:
                    writes += 1
                    write_command_counts[normalized] = (
                        write_command_counts.get(normalized, 0) + 1
                    )
                elif verb in EXEC_VERBS:
                    execs += 1
                else:
                    others += 1
    except OSError as exc:
        return {"error": str(exc), "action_trace": str(trace_path)}

    if total == 0:
        return {"error": "no command entries found", "action_trace": str(trace_path)}

    retry_count = sum(count - 1 for count in write_command_counts.values() if count > 1)
    ratio = None
    if writes:
        ratio = round(reads / writes, 4)

    return {
        "read_count": reads,
        "write_count": writes,
        "exec_count": execs,
        "other_count": others,
        "total_commands": total,
        "retry_count": retry_count,
        "read_write_ratio": ratio,
        "action_trace": str(trace_path),
    }
