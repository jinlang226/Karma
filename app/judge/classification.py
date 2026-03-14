import re


_NUMERIC_OPS = {"gt", "gte", "lt", "lte"}
_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_FALSY_VALUES = {"0", "false", "no", "off", ""}
_SUPPORTED_OPS = {
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "exists",
    "not_exists",
    "is_true",
    "is_false",
    "truthy",
    "falsy",
    "regex",
    "starts_with",
    "ends_with",
}


def evaluate_classifiers(rubric, judge_input, scores):
    classifiers = (rubric or {}).get("classifiers") or []
    if not isinstance(classifiers, list) or not classifiers:
        return {}

    context = _build_context(judge_input, scores)
    out = {}
    for item in classifiers:
        if not isinstance(item, dict):
            continue
        classifier_id = str(item.get("id") or "").strip()
        if not classifier_id:
            continue
        out[classifier_id] = _evaluate_classifier(item, context)
    return out


def _build_context(judge_input, scores):
    judge_input = judge_input if isinstance(judge_input, dict) else {}
    scores = scores if isinstance(scores, dict) else {}

    question_map = {}
    for row in scores.get("normalized_dimension_scores") or []:
        if not isinstance(row, dict):
            continue
        qid = str(row.get("id") or "").strip()
        if not qid:
            continue
        evidence_ids = [value for value in (row.get("evidence_ids") or []) if isinstance(value, str) and value.strip()]
        question_map[qid] = {
            "score": row.get("score"),
            "confidence": row.get("confidence"),
            "evidence_ids": evidence_ids,
            "has_evidence": bool(evidence_ids),
            "evidence_count": len(evidence_ids),
        }

    workflow_context = judge_input.get("workflow_context")
    if not isinstance(workflow_context, dict):
        workflow_context = {}

    blocks = judge_input.get("blocks")
    if not isinstance(blocks, dict):
        blocks = {}
    final_sweep = blocks.get("workflow_final_sweep")
    if not isinstance(final_sweep, dict):
        final_sweep = {}

    regression_counts = _count_regression_classifications(final_sweep)

    return {
        "q": question_map,
        "s": {
            "final_score": scores.get("final_score"),
            "process_quality_score": scores.get("process_quality_score"),
            "efficiency_score": scores.get("efficiency_score"),
            "average_confidence": scores.get("average_confidence"),
            "scored_dimensions": scores.get("scored_dimensions"),
            "total_dimensions": scores.get("total_dimensions"),
        },
        "w": {
            "workflow_enabled": bool(workflow_context.get("workflow_enabled")),
            "workflow_id": workflow_context.get("workflow_id"),
            "mode": workflow_context.get("mode"),
            "stage_total": workflow_context.get("stage_total"),
            "active_stage_id": workflow_context.get("active_stage_id"),
            "active_stage_index": workflow_context.get("active_stage_index"),
            "terminal": workflow_context.get("terminal"),
            "terminal_reason": workflow_context.get("terminal_reason"),
            "solve_status": workflow_context.get("solve_status"),
            "regression_total_count": regression_counts["total"],
            "regression_expected_count": regression_counts["expected"],
            "regression_unexpected_count": regression_counts["unexpected"],
            "regression_unknown_count": regression_counts["unknown"],
        },
        "m": judge_input.get("meta") if isinstance(judge_input.get("meta"), dict) else {},
    }


def _count_regression_classifications(final_sweep):
    counts = {"total": 0, "expected": 0, "unexpected": 0, "unknown": 0}
    if not isinstance(final_sweep, dict):
        return counts

    regression = final_sweep.get("regression")
    if isinstance(regression, dict):
        for value in regression.values():
            cls = ""
            if isinstance(value, dict):
                cls = str(value.get("classification") or "").strip().lower()
            _accumulate_regression_classification(cls, counts)
        return counts

    stages = final_sweep.get("stages")
    if isinstance(stages, list):
        for row in stages:
            if not isinstance(row, dict):
                continue
            cls = str(row.get("regression_classification") or row.get("classification") or "").strip().lower()
            _accumulate_regression_classification(cls, counts)
    return counts


def _accumulate_regression_classification(classification, counts):
    classification = str(classification or "").strip().lower()
    if not classification:
        return
    counts["total"] += 1
    if "unexpected" in classification:
        counts["unexpected"] += 1
        return
    if "expected" in classification:
        counts["expected"] += 1
        return
    counts["unknown"] += 1


def _evaluate_classifier(classifier, context):
    labels = [str(item.get("id") or "").strip() for item in (classifier.get("labels") or []) if isinstance(item, dict)]
    labels = [item for item in labels if item]
    default_label = str(classifier.get("default_label") or "").strip() or (labels[0] if labels else "unknown")
    unknown_label = "unknown" if "unknown" in labels else default_label

    scope = classifier.get("scope") if isinstance(classifier.get("scope"), dict) else {}
    scope_match = _scope_matches(scope, context)
    if not scope_match:
        return {
            "label": default_label,
            "rule_id": None,
            "status": "skipped_scope",
            "confidence": _clamp_confidence(context.get("s", {}).get("average_confidence"), default=0.5),
            "evidence_ids": [],
            "rationale": "classifier scope did not match run context",
            "scope_applied": {"matched": False, "scope": scope},
        }

    matched_rule = None
    for rule in classifier.get("rules") or []:
        if not isinstance(rule, dict):
            continue
        matched, refs_used, question_ids = _evaluate_rule(rule, context)
        if matched:
            matched_rule = {
                "rule_id": str(rule.get("id") or "").strip() or None,
                "label": str(rule.get("label") or "").strip() or default_label,
                "refs_used": refs_used,
                "question_ids": question_ids,
            }
            break

    if matched_rule is None:
        return {
            "label": default_label,
            "rule_id": None,
            "status": "defaulted",
            "confidence": _clamp_confidence(context.get("s", {}).get("average_confidence"), default=0.5),
            "evidence_ids": [],
            "rationale": "no classifier rule matched",
            "scope_applied": {"matched": True, "scope": scope},
        }

    qmap = context.get("q", {})
    evidence_ids = []
    confidence_values = []
    for qid in matched_rule.get("question_ids") or []:
        row = qmap.get(qid) if isinstance(qmap, dict) else None
        if not isinstance(row, dict):
            continue
        confidence_values.append(_clamp_confidence(row.get("confidence"), default=None))
        for evidence_id in row.get("evidence_ids") or []:
            if evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)

    fallback_conf = _clamp_confidence(context.get("s", {}).get("average_confidence"), default=0.5)
    confidences = [item for item in confidence_values if item is not None]
    confidence = fallback_conf if not confidences else sum(confidences) / len(confidences)

    label = matched_rule["label"]
    status = "matched"
    unknown_policy = classifier.get("unknown_policy") if isinstance(classifier.get("unknown_policy"), dict) else {}
    if _is_truthy(unknown_policy.get("on_missing_evidence")) and not evidence_ids:
        label = unknown_label
        status = "unknown_missing_evidence"
    min_confidence = unknown_policy.get("min_confidence")
    try:
        min_confidence = float(min_confidence)
    except Exception:
        min_confidence = None
    if min_confidence is not None and confidence < min_confidence:
        label = unknown_label
        status = "unknown_low_confidence"

    return {
        "label": label,
        "rule_id": matched_rule["rule_id"],
        "status": status,
        "confidence": round(float(confidence), 4),
        "evidence_ids": evidence_ids,
        "rationale": _build_rationale(label=label, status=status, matched_rule=matched_rule),
        "scope_applied": {"matched": True, "scope": scope},
    }


def _build_rationale(label, status, matched_rule):
    rule_id = matched_rule.get("rule_id")
    if status == "matched":
        if rule_id:
            return f"matched rule '{rule_id}' -> label '{label}'"
        return f"matched rule -> label '{label}'"
    if status == "unknown_missing_evidence":
        return "matched rule but required evidence was missing; coerced to unknown label"
    if status == "unknown_low_confidence":
        return "matched rule but confidence was below configured threshold; coerced to unknown label"
    return f"classification resolved to '{label}'"


def _scope_matches(scope, context):
    if not isinstance(scope, dict) or not scope:
        return True
    workflow = context.get("w") if isinstance(context.get("w"), dict) else {}
    if "workflow_only" in scope and bool(scope.get("workflow_only")) and not bool(workflow.get("workflow_enabled")):
        return False
    stage_ids = scope.get("stage_ids")
    if isinstance(stage_ids, list) and stage_ids:
        normalized = {str(item).strip() for item in stage_ids if str(item).strip()}
        active_stage_id = str(workflow.get("active_stage_id") or "").strip()
        if active_stage_id not in normalized:
            return False
    return True


def _evaluate_rule(rule, context):
    refs_used = []
    question_ids = set()

    def _eval_clause(clause):
        matched, refs, qids = _evaluate_clause(clause, context)
        refs_used.extend(refs)
        question_ids.update(qids)
        return matched

    has_clause = False
    matched = True
    if "when" in rule:
        has_clause = True
        matched = matched and _eval_clause(rule.get("when"))
    if "all" in rule:
        has_clause = True
        all_items = rule.get("all")
        if isinstance(all_items, list):
            matched = matched and all(_eval_clause(item) for item in all_items)
        else:
            matched = False
    if "any" in rule:
        has_clause = True
        any_items = rule.get("any")
        if isinstance(any_items, list):
            matched = matched and any(_eval_clause(item) for item in any_items)
        else:
            matched = False
    if "none" in rule:
        has_clause = True
        none_items = rule.get("none")
        if isinstance(none_items, list):
            matched = matched and all(not _eval_clause(item) for item in none_items)
        else:
            matched = False

    # Backward-compatible shorthand: allow rule itself to be one condition.
    if not has_clause:
        matched = _eval_clause(rule)

    return bool(matched), refs_used, sorted(question_ids)


def _evaluate_clause(clause, context):
    if not isinstance(clause, dict):
        return False, [], set()

    # Nested group support.
    if any(key in clause for key in ("when", "all", "any", "none")) and "ref" not in clause:
        temp_rule = {}
        for key in ("when", "all", "any", "none"):
            if key in clause:
                temp_rule[key] = clause.get(key)
        matched, refs, qids = _evaluate_rule(temp_rule, context)
        return matched, refs, set(qids)

    ref = str(clause.get("ref") or "").strip()
    if not ref:
        return False, [], set()

    op = str(clause.get("op") or "eq").strip().lower().replace("-", "_")
    if op not in _SUPPORTED_OPS:
        op = "eq"

    actual, question_id = _resolve_ref_value(ref, context)
    expected = clause.get("value")
    matched = _apply_operator(actual, op, expected)
    qids = {question_id} if question_id else set()
    return matched, [ref], qids


def _resolve_ref_value(ref, context):
    parts = [item for item in str(ref or "").split(".") if item]
    if len(parts) < 2:
        return None, None
    root = parts[0]
    if root == "q":
        qid = parts[1]
        qmap = context.get("q") if isinstance(context.get("q"), dict) else {}
        row = qmap.get(qid)
        if not isinstance(row, dict):
            return None, qid
        if len(parts) == 2:
            return row, qid
        key = parts[2]
        return row.get(key), qid

    root_obj = context.get(root) if isinstance(context, dict) else None
    value = root_obj
    for key in parts[1:]:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return None, None
    return value, None


def _apply_operator(actual, op, expected):
    if op == "exists":
        return actual is not None and str(actual).strip() != ""
    if op == "not_exists":
        return actual is None or str(actual).strip() == ""
    if op in {"is_true", "truthy"}:
        return _is_truthy(actual)
    if op in {"is_false", "falsy"}:
        return _is_falsy(actual)

    if op in _NUMERIC_OPS:
        actual_num = _to_number(actual)
        expected_num = _to_number(expected)
        if actual_num is None or expected_num is None:
            return False
        if op == "gt":
            return actual_num > expected_num
        if op == "gte":
            return actual_num >= expected_num
        if op == "lt":
            return actual_num < expected_num
        return actual_num <= expected_num

    if op == "eq":
        return actual == expected
    if op == "neq":
        return actual != expected

    if op == "in":
        if isinstance(expected, (list, tuple, set)):
            return actual in expected
        return False
    if op == "not_in":
        if isinstance(expected, (list, tuple, set)):
            return actual not in expected
        return False

    if op == "contains":
        if isinstance(actual, str):
            return str(expected) in actual
        if isinstance(actual, dict):
            return expected in actual
        if isinstance(actual, (list, tuple, set)):
            return expected in actual
        return False
    if op == "not_contains":
        if isinstance(actual, str):
            return str(expected) not in actual
        if isinstance(actual, dict):
            return expected not in actual
        if isinstance(actual, (list, tuple, set)):
            return expected not in actual
        return True

    if op == "regex":
        if actual is None:
            return False
        try:
            return re.search(str(expected), str(actual)) is not None
        except Exception:
            return False

    if op == "starts_with":
        if actual is None:
            return False
        return str(actual).startswith(str(expected))
    if op == "ends_with":
        if actual is None:
            return False
        return str(actual).endswith(str(expected))

    return False


def _to_number(value):
    try:
        return float(value)
    except Exception:
        return None


def _is_truthy(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in _TRUTHY_VALUES


def _is_falsy(value):
    if isinstance(value, bool):
        return not value
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return value == 0
    return str(value).strip().lower() in _FALSY_VALUES


def _clamp_confidence(value, default):
    try:
        num = float(value)
    except Exception:
        return default
    if num < 0:
        return 0.0
    if num > 1:
        return 1.0
    return num
