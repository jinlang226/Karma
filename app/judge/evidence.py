import re


_AGENT_LOG_RANGE_RE = re.compile(r"^agent\.log:L(\d+)-L(\d+)$")
_METRIC_PATH_RE = re.compile(
    r"^(external_metrics|agent_usage|efficiency_facts|workflow_efficiency_facts):([A-Za-z0-9_.-]+)$"
)


def _parse_agent_log_range(evidence_id):
    match = _AGENT_LOG_RANGE_RE.match(str(evidence_id or "").strip())
    if not match:
        return None
    try:
        start = int(match.group(1))
        end = int(match.group(2))
    except Exception:
        return None
    return start, end


def _path_exists(obj, dotted_path):
    current = obj
    for part in str(dotted_path or "").split("."):
        if not part:
            return False
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return False
    return True


def _workflow_path_exists(workflow_context, dotted_path):
    return _path_exists(workflow_context, dotted_path)


def validate_evidence_ids(dimension_scores, judge_input):
    blocks = (judge_input or {}).get("blocks") if isinstance(judge_input, dict) else {}
    blocks = blocks if isinstance(blocks, dict) else {}
    line_count = int(((blocks.get("agent_log") or {}).get("line_count")) or 0)
    sources = {
        "external_metrics": blocks.get("external_metrics"),
        "agent_usage": blocks.get("agent_usage"),
        "efficiency_facts": blocks.get("efficiency_facts"),
        "workflow_efficiency_facts": blocks.get("workflow_efficiency_facts"),
    }
    workflow_context = (judge_input or {}).get("workflow_context")
    workflow_context = workflow_context if isinstance(workflow_context, dict) else {}

    result = {
        "line_count": line_count,
        "validated_scope": "agent.log|external_metrics|agent_usage|efficiency_facts|workflow_efficiency_facts|workflow",
        "valid_count": 0,
        "invalid_count": 0,
        "unvalidated_count": 0,
        "invalid": [],
        "unvalidated": [],
    }

    for item in dimension_scores or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "").strip()
        for evidence_id in item.get("evidence_ids") or []:
            if not isinstance(evidence_id, str):
                result["invalid_count"] += 1
                result["invalid"].append(
                    {
                        "dimension_id": qid,
                        "evidence_id": evidence_id,
                        "reason": "non_string",
                    }
                )
                continue

            ref = evidence_id.strip()
            if not ref:
                result["invalid_count"] += 1
                result["invalid"].append(
                    {
                        "dimension_id": qid,
                        "evidence_id": evidence_id,
                        "reason": "empty",
                    }
                )
                continue

            if not ref.startswith("agent.log:"):
                if ref.startswith("workflow:"):
                    dotted = ref[len("workflow:") :].strip()
                    if not dotted:
                        result["invalid_count"] += 1
                        result["invalid"].append(
                            {
                                "dimension_id": qid,
                                "evidence_id": evidence_id,
                                "reason": "bad_workflow_format",
                            }
                        )
                        continue
                    if not _workflow_path_exists(workflow_context, dotted):
                        result["invalid_count"] += 1
                        result["invalid"].append(
                            {
                                "dimension_id": qid,
                                "evidence_id": evidence_id,
                                "reason": "missing_workflow_path",
                            }
                        )
                        continue
                    result["valid_count"] += 1
                    continue

                metric_ref = _METRIC_PATH_RE.match(ref)
                if metric_ref:
                    source_name = metric_ref.group(1)
                    dotted = metric_ref.group(2)
                    root = sources.get(source_name)
                    if not isinstance(root, dict):
                        result["invalid_count"] += 1
                        result["invalid"].append(
                            {
                                "dimension_id": qid,
                                "evidence_id": evidence_id,
                                "reason": "missing_source",
                            }
                        )
                        continue
                    if not _path_exists(root, dotted):
                        result["invalid_count"] += 1
                        result["invalid"].append(
                            {
                                "dimension_id": qid,
                                "evidence_id": evidence_id,
                                "reason": "missing_path",
                            }
                        )
                        continue
                    result["valid_count"] += 1
                    continue

                result["unvalidated_count"] += 1
                result["unvalidated"].append(
                    {
                        "dimension_id": qid,
                        "evidence_id": evidence_id,
                        "reason": "unsupported_reference_prefix",
                    }
                )
                continue

            parsed = _parse_agent_log_range(ref)
            if not parsed:
                result["invalid_count"] += 1
                result["invalid"].append(
                    {
                        "dimension_id": qid,
                        "evidence_id": evidence_id,
                        "reason": "bad_format",
                    }
                )
                continue

            start, end = parsed
            if start <= 0 or end <= 0:
                result["invalid_count"] += 1
                result["invalid"].append(
                    {
                        "dimension_id": qid,
                        "evidence_id": evidence_id,
                        "reason": "non_positive_range",
                    }
                )
                continue
            if end < start:
                result["invalid_count"] += 1
                result["invalid"].append(
                    {
                        "dimension_id": qid,
                        "evidence_id": evidence_id,
                        "reason": "reversed_range",
                    }
                )
                continue
            if line_count > 0 and (start > line_count or end > line_count):
                result["invalid_count"] += 1
                result["invalid"].append(
                    {
                        "dimension_id": qid,
                        "evidence_id": evidence_id,
                        "reason": "out_of_range",
                    }
                )
                continue

            result["valid_count"] += 1

    return result
