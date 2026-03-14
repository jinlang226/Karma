"""
Spark Job Fix Efficiency Metric

Measures how efficiently an operator fixes Spark job issues:
- Tracks fix attempts (job deletions and re-creations)
- Measures time to successful job completion
- Counts unnecessary resource modifications
"""

import json
import shlex
from pathlib import Path

from ..settings import ACTION_TRACE_LOG


def _command_from_record(record):
    command = record.get("command")
    if command is None and "args" in record:
        command = record.get("args")
    if command is None:
        return None
    if isinstance(command, list):
        return " ".join(str(part) for part in command if part is not None)
    return str(command)


def _parse_trace(trace_path):
    """Parse action trace to extract Spark-related operations."""
    job_deletes = 0
    job_applies = 0
    role_patches = 0
    deployment_patches = 0
    other_writes = 0
    first_mutation_ts = None
    last_mutation_ts = None

    if not trace_path or not trace_path.exists():
        return None, "action trace not found"

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

                ts = record.get("ts")
                try:
                    tokens = shlex.split(cmd)
                except ValueError:
                    tokens = cmd.split()

                if len(tokens) < 2 or tokens[0] != "kubectl":
                    continue

                verb = tokens[1] if len(tokens) > 1 else None
                is_write = verb in {"apply", "patch", "delete", "create", "replace", "edit", "scale"}

                if is_write:
                    if first_mutation_ts is None:
                        first_mutation_ts = ts
                    last_mutation_ts = ts

                    # Check resource type
                    cmd_lower = cmd.lower()
                    if "job" in cmd_lower:
                        if verb == "delete":
                            job_deletes += 1
                        elif verb in {"apply", "create"}:
                            job_applies += 1
                    elif "role" in cmd_lower and verb == "patch":
                        role_patches += 1
                    elif "deployment" in cmd_lower and verb == "patch":
                        deployment_patches += 1
                    else:
                        other_writes += 1

    except OSError as exc:
        return None, str(exc)

    return {
        "job_delete_count": job_deletes,
        "job_apply_count": job_applies,
        "role_patch_count": role_patches,
        "deployment_patch_count": deployment_patches,
        "other_write_count": other_writes,
        "first_mutation_ts": first_mutation_ts,
        "last_mutation_ts": last_mutation_ts,
    }, None


def compute(meta, run_dir, trace_path=None):
    """
    Compute Spark job fix efficiency metrics.

    Returns:
        dict with:
        - job_fix_attempts: Number of job delete+apply cycles
        - unnecessary_modifications: Changes that weren't needed
        - fix_efficiency_score: 0-1 score (higher is better)
    """
    if trace_path is None:
        trace_path = ACTION_TRACE_LOG
    else:
        trace_path = Path(trace_path)

    stats, err = _parse_trace(trace_path)
    if err:
        return {"error": err, "action_trace": str(trace_path)}

    # Calculate fix attempts (each delete-apply cycle is one attempt)
    fix_attempts = max(stats["job_delete_count"], stats["job_apply_count"])

    # Calculate efficiency score
    # Ideal: 1 fix attempt, minimal patches
    # Penalty for multiple attempts and unnecessary patches
    total_operations = (
        stats["job_delete_count"]
        + stats["job_apply_count"]
        + stats["role_patch_count"]
        + stats["deployment_patch_count"]
        + stats["other_write_count"]
    )

    # Expected minimum operations for deploy_spark_pi: 1 delete + 1 apply + 1 role patch = 3
    expected_min_ops = 3
    if total_operations == 0:
        efficiency_score = 0.0
    elif total_operations <= expected_min_ops:
        efficiency_score = 1.0
    else:
        # Penalize extra operations
        efficiency_score = max(0.0, 1.0 - (total_operations - expected_min_ops) * 0.1)

    return {
        "job_fix_attempts": fix_attempts,
        "job_delete_count": stats["job_delete_count"],
        "job_apply_count": stats["job_apply_count"],
        "role_patch_count": stats["role_patch_count"],
        "deployment_patch_count": stats["deployment_patch_count"],
        "other_write_count": stats["other_write_count"],
        "total_write_operations": total_operations,
        "fix_efficiency_score": round(efficiency_score, 4),
        "action_trace": str(trace_path),
    }
