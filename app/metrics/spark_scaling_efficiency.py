"""
Spark Scaling Efficiency Metric

Measures how efficiently an operator scales Spark workers in response to load:
- Tracks scaling operations (kubectl scale commands)
- Measures scaling responsiveness
- Evaluates if scaling matched the required phases
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


def _extract_replica_count(cmd):
    """Extract replica count from a scale command."""
    # Match patterns like --replicas=10 or --replicas 10
    match = re.search(r"--replicas[=\s]+(\d+)", cmd)
    if match:
        return int(match.group(1))
    return None


def _parse_trace(trace_path):
    """Parse action trace to extract scaling operations."""
    scaling_events = []
    first_scale_ts = None
    last_scale_ts = None

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

                # Look for scale commands
                if "scale" in tokens and "spark-worker" in cmd.lower():
                    replicas = _extract_replica_count(cmd)
                    if replicas is not None:
                        if first_scale_ts is None:
                            first_scale_ts = ts
                        last_scale_ts = ts
                        scaling_events.append({
                            "timestamp": ts,
                            "replicas": replicas,
                            "command": cmd,
                        })

    except OSError as exc:
        return None, str(exc)

    return {
        "scaling_events": scaling_events,
        "first_scale_ts": first_scale_ts,
        "last_scale_ts": last_scale_ts,
    }, None


def compute(meta, run_dir, trace_path=None):
    """
    Compute Spark scaling efficiency metrics.

    Expected scaling pattern for spark_streaming_autoscale:
    - Phase 1: 5 workers (initial)
    - Phase 2: Scale to 10 workers
    - Phase 3: Scale to 20 workers
    - Phase 4: Scale back to 5 workers

    Returns:
        dict with:
        - scaling_event_count: Number of scaling operations
        - scaling_sequence: List of replica counts in order
        - phase_coverage: How many expected phases were covered
        - scaling_efficiency_score: 0-1 score
    """
    if trace_path is None:
        trace_path = ACTION_TRACE_LOG
    else:
        trace_path = Path(trace_path)

    stats, err = _parse_trace(trace_path)
    if err:
        return {"error": err, "action_trace": str(trace_path)}

    scaling_events = stats["scaling_events"]
    scaling_sequence = [e["replicas"] for e in scaling_events]

    # Expected scaling phases: 5 -> 10 -> 20 -> 5
    expected_targets = [10, 20, 5]
    phases_hit = 0

    for target in expected_targets:
        if target in scaling_sequence:
            phases_hit += 1

    # Check if scaling was done in the right order
    correct_order = True
    if len(scaling_sequence) >= 3:
        # Should see 10 before 20, and 20 before final 5
        try:
            idx_10 = scaling_sequence.index(10) if 10 in scaling_sequence else -1
            idx_20 = scaling_sequence.index(20) if 20 in scaling_sequence else -1
            # Find the last occurrence of 5 (cooldown)
            idx_5_final = -1
            for i in range(len(scaling_sequence) - 1, -1, -1):
                if scaling_sequence[i] == 5:
                    idx_5_final = i
                    break
            if idx_10 >= 0 and idx_20 >= 0 and idx_5_final >= 0:
                correct_order = idx_10 < idx_20 < idx_5_final
        except (ValueError, IndexError):
            correct_order = False

    # Calculate efficiency score
    # Perfect: 3 scaling events hitting all phases in order
    if len(scaling_events) == 0:
        efficiency_score = 0.0
    else:
        base_score = phases_hit / len(expected_targets)
        if correct_order:
            efficiency_score = base_score
        else:
            efficiency_score = base_score * 0.7  # Penalty for wrong order

        # Penalty for excessive scaling operations
        if len(scaling_events) > 4:
            efficiency_score *= 0.9

    return {
        "scaling_event_count": len(scaling_events),
        "scaling_sequence": scaling_sequence,
        "scaling_events": scaling_events,
        "expected_phases": expected_targets,
        "phases_hit": phases_hit,
        "correct_order": correct_order,
        "scaling_efficiency_score": round(efficiency_score, 4),
        "first_scale_ts": stats["first_scale_ts"],
        "last_scale_ts": stats["last_scale_ts"],
        "action_trace": str(trace_path),
    }
