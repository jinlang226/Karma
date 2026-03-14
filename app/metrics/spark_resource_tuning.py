"""
Spark Resource Tuning Metric

Measures how effectively an operator tunes Spark resources to fix OOM issues:
- Tracks memory/resource configuration changes
- Monitors worker scaling decisions
- Evaluates if AQE (Adaptive Query Execution) was enabled
"""

import json
import re
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


def _parse_memory_value(value):
    """Parse memory value like '1g', '512m', '2048Mi' to MB."""
    if not value:
        return None
    value = str(value).lower().strip()
    match = re.match(r"(\d+(?:\.\d+)?)\s*(g|gi|m|mi|k|ki)?", value)
    if not match:
        return None
    num = float(match.group(1))
    unit = match.group(2) or "m"
    if unit in ("g", "gi"):
        return int(num * 1024)
    elif unit in ("m", "mi"):
        return int(num)
    elif unit in ("k", "ki"):
        return int(num / 1024)
    return int(num)


def _extract_memory_from_cmd(cmd):
    """Extract memory settings from kubectl or spark-submit command."""
    memory_settings = []

    # Match patterns like --executor-memory 1g, SPARK_WORKER_MEMORY=2G
    patterns = [
        (r"--executor-memory[=\s]+([^\s]+)", "executor_memory"),
        (r"--driver-memory[=\s]+([^\s]+)", "driver_memory"),
        (r"SPARK_WORKER_MEMORY[=\s]+([^\s]+)", "worker_memory"),
        (r"spark\.executor\.memory[=\s]+([^\s]+)", "executor_memory"),
    ]

    for pattern, name in patterns:
        match = re.search(pattern, cmd, re.IGNORECASE)
        if match:
            memory_settings.append({
                "type": name,
                "value": match.group(1),
                "value_mb": _parse_memory_value(match.group(1)),
            })

    return memory_settings


def _extract_replica_count(cmd):
    """Extract replica count from a scale command."""
    match = re.search(r"--replicas[=\s]+(\d+)", cmd)
    if match:
        return int(match.group(1))
    return None


def _check_aqe_enabled(cmd):
    """Check if AQE (Adaptive Query Execution) is enabled in the command."""
    aqe_patterns = [
        r"spark\.sql\.adaptive\.enabled[=\s]+true",
        r"spark\.sql\.adaptive\.skewJoin\.enabled[=\s]+true",
    ]
    for pattern in aqe_patterns:
        if re.search(pattern, cmd, re.IGNORECASE):
            return True
    return False


def _parse_trace(trace_path):
    """Parse action trace to extract resource tuning operations."""
    memory_changes = []
    scaling_events = []
    aqe_enabled = False
    job_deletes = 0
    job_applies = 0
    env_sets = 0

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

                # Check for memory configurations
                mem_settings = _extract_memory_from_cmd(cmd)
                if mem_settings:
                    memory_changes.append({
                        "timestamp": ts,
                        "settings": mem_settings,
                        "command": cmd,
                    })

                # Check for AQE
                if _check_aqe_enabled(cmd):
                    aqe_enabled = True

                # Check for scaling
                if verb == "scale" and "spark-worker" in cmd.lower():
                    replicas = _extract_replica_count(cmd)
                    if replicas is not None:
                        scaling_events.append({
                            "timestamp": ts,
                            "replicas": replicas,
                        })

                # Check for job operations
                if "job" in cmd.lower() or "etl-job" in cmd.lower():
                    if verb == "delete":
                        job_deletes += 1
                    elif verb in {"apply", "create"}:
                        job_applies += 1

                # Check for env set (memory tuning)
                if verb == "set" and "env" in cmd.lower():
                    env_sets += 1
                    mem_settings = _extract_memory_from_cmd(cmd)
                    if mem_settings:
                        memory_changes.append({
                            "timestamp": ts,
                            "settings": mem_settings,
                            "command": cmd,
                        })

    except OSError as exc:
        return None, str(exc)

    return {
        "memory_changes": memory_changes,
        "scaling_events": scaling_events,
        "aqe_enabled": aqe_enabled,
        "job_deletes": job_deletes,
        "job_applies": job_applies,
        "env_sets": env_sets,
    }, None


def compute(meta, run_dir, trace_path=None):
    """
    Compute Spark resource tuning metrics.

    For spark_etl_skew_oom task, evaluates:
    - Memory increases (from 256m to >= 512m)
    - Worker scaling (from 2 to >= 4)
    - AQE enablement

    Returns:
        dict with:
        - memory_increased: Whether memory was increased
        - workers_scaled: Whether workers were scaled up
        - aqe_enabled: Whether AQE was configured
        - tuning_strategy: Identified strategy (memory/scaling/aqe/combined)
        - tuning_efficiency_score: 0-1 score
    """
    if trace_path is None:
        trace_path = ACTION_TRACE_LOG
    else:
        trace_path = Path(trace_path)

    stats, err = _parse_trace(trace_path)
    if err:
        return {"error": err, "action_trace": str(trace_path)}

    # Analyze memory changes
    memory_increased = False
    max_memory_mb = 0
    for change in stats["memory_changes"]:
        for setting in change["settings"]:
            if setting["value_mb"] and setting["value_mb"] > 256:
                memory_increased = True
                max_memory_mb = max(max_memory_mb, setting["value_mb"])

    # Analyze scaling
    workers_scaled = False
    max_workers = 2  # Initial value
    for event in stats["scaling_events"]:
        if event["replicas"] > 2:
            workers_scaled = True
            max_workers = max(max_workers, event["replicas"])

    # Determine strategy
    strategies_used = []
    if memory_increased:
        strategies_used.append("memory_increase")
    if workers_scaled:
        strategies_used.append("worker_scaling")
    if stats["aqe_enabled"]:
        strategies_used.append("aqe")

    if len(strategies_used) == 0:
        tuning_strategy = "none"
    elif len(strategies_used) == 1:
        tuning_strategy = strategies_used[0]
    else:
        tuning_strategy = "combined"

    # Calculate efficiency score
    # Any valid fix strategy is good
    if tuning_strategy == "none":
        efficiency_score = 0.0
    else:
        efficiency_score = 0.7  # Base score for fixing the issue

        # Bonus for using multiple strategies
        if len(strategies_used) >= 2:
            efficiency_score += 0.2

        # Bonus for minimal retry attempts
        if stats["job_deletes"] <= 2:
            efficiency_score += 0.1

        efficiency_score = min(1.0, efficiency_score)

    return {
        "memory_increased": memory_increased,
        "max_memory_mb": max_memory_mb,
        "workers_scaled": workers_scaled,
        "max_workers": max_workers,
        "aqe_enabled": stats["aqe_enabled"],
        "tuning_strategy": tuning_strategy,
        "strategies_used": strategies_used,
        "memory_changes": stats["memory_changes"],
        "scaling_events": stats["scaling_events"],
        "job_delete_count": stats["job_deletes"],
        "job_apply_count": stats["job_applies"],
        "tuning_efficiency_score": round(efficiency_score, 4),
        "action_trace": str(trace_path),
    }
