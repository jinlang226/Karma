import copy
from pathlib import Path

import yaml

from app.settings import RESOURCES_DIR


DEFAULT_RUBRIC = {
    "rubric_id": "default.trajectory.v1",
    "rubric_version": "1",
    "objective_weights": {
        "process_quality": 0.7,
        "efficiency": 0.3,
    },
    "questions": [
        {
            "id": "diagnosis_speed",
            "track": "process_quality",
            "weight": 0.25,
            "prompt": "How quickly did the agent converge on the true root cause?",
        },
        {
            "id": "hypothesis_quality",
            "track": "process_quality",
            "weight": 0.25,
            "prompt": "Were hypotheses explicit, testable, and updated from evidence?",
        },
        {
            "id": "debugging_discipline",
            "track": "process_quality",
            "weight": 0.25,
            "prompt": "Did the agent verify assumptions with targeted checks instead of guessing?",
        },
        {
            "id": "fix_robustness",
            "track": "process_quality",
            "weight": 0.25,
            "prompt": "Was the proposed fix durable and aligned with the problem constraints?",
        },
        {
            "id": "resource_efficiency",
            "track": "efficiency",
            "weight": 1.0,
            "prompt": "How efficient was the trajectory in time/commands/tokens while maintaining quality?",
        },
    ],
    "milestones": [
        "Identify likely root cause and validate it with concrete evidence.",
        "Apply a fix aligned to the challenge constraints.",
        "Verify fix outcome and avoid unsafe/distracting mutations.",
    ],
    "anti_patterns": [
        "Repeated blind retries without new evidence.",
        "Guessing fixes without validating assumptions.",
        "Bypassing the intended challenge path.",
    ],
    "prompt_notes": [
        "Avoid outcome bias: process quality should be judged from evidence, not final status alone.",
        "Cite evidence snippet IDs for every dimension score.",
    ],
    "classifiers": [],
}

_ALLOWED_TRACKS = {"process_quality", "efficiency"}
_BASE_RESERVED_KEYS = {"defaults", "profiles", "meta", "metadata"}
_ALLOWED_CLASSIFIER_OPS = {
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


def _read_yaml(path):
    try:
        return yaml.safe_load(Path(path).read_text())
    except Exception:
        return None


def _to_float(value, default):
    try:
        num = float(value)
    except Exception:
        return float(default)
    if num < 0:
        return float(default)
    return num


def _normalize_objective_weights(raw):
    base = copy.deepcopy(DEFAULT_RUBRIC["objective_weights"])
    if not isinstance(raw, dict):
        return base
    for key in ("process_quality", "efficiency"):
        if key in raw:
            base[key] = _to_float(raw.get(key), base[key])
    total = base["process_quality"] + base["efficiency"]
    if total <= 0:
        return copy.deepcopy(DEFAULT_RUBRIC["objective_weights"])
    base["process_quality"] = base["process_quality"] / total
    base["efficiency"] = base["efficiency"] / total
    return base


def _normalize_question(item):
    if not isinstance(item, dict):
        return None
    qid = str(item.get("id") or "").strip()
    prompt = str(item.get("prompt") or "").strip()
    if not qid or not prompt:
        return None
    track = str(item.get("track") or "process_quality").strip()
    if track not in _ALLOWED_TRACKS:
        track = "process_quality"
    weight = _to_float(item.get("weight"), 1.0)
    if weight <= 0:
        weight = 1.0
    return {
        "id": qid,
        "track": track,
        "weight": weight,
        "prompt": prompt,
    }


def _normalize_text_list(items):
    out = []
    if not isinstance(items, list):
        return out
    for item in items:
        value = str(item).strip()
        if value and value not in out:
            out.append(value)
    return out


def _normalize_classifier_labels(items):
    out = []
    seen = set()
    if not isinstance(items, list):
        return out
    for item in items:
        label_id = ""
        description = ""
        if isinstance(item, str):
            label_id = item.strip()
        elif isinstance(item, dict):
            label_id = str(item.get("id") or item.get("label") or "").strip()
            description = str(item.get("description") or "").strip()
        if not label_id or label_id in seen:
            continue
        seen.add(label_id)
        row = {"id": label_id}
        if description:
            row["description"] = description
        out.append(row)
    return out


def _normalize_classifier_condition(item):
    if not isinstance(item, dict):
        return None

    ref = str(item.get("ref") or "").strip()
    if ref:
        op = str(item.get("op") or "eq").strip().lower().replace("-", "_")
        if op not in _ALLOWED_CLASSIFIER_OPS:
            op = "eq"
        out = {"ref": ref, "op": op}
        if "value" in item:
            out["value"] = copy.deepcopy(item.get("value"))
        return out

    out = {}
    when = _normalize_classifier_condition(item.get("when")) if "when" in item else None
    if when:
        out["when"] = when

    for key in ("all", "any", "none"):
        values = item.get(key)
        if not isinstance(values, list):
            continue
        normalized = [_normalize_classifier_condition(value) for value in values]
        normalized = [value for value in normalized if value]
        if normalized:
            out[key] = normalized
    return out or None


def _normalize_classifier_rule(item):
    if not isinstance(item, dict):
        return None
    label = str(item.get("label") or "").strip()
    if not label:
        return None
    cond_in = {}
    for key in ("when", "all", "any", "none", "ref", "op", "value"):
        if key in item:
            cond_in[key] = item.get(key)
    condition = _normalize_classifier_condition(cond_in)
    if not condition:
        return None
    out = {"label": label, **condition}
    rule_id = str(item.get("id") or "").strip()
    if rule_id:
        out["id"] = rule_id
    return out


def _normalize_classifier(item):
    if not isinstance(item, dict):
        return None
    cid = str(item.get("id") or "").strip()
    if not cid:
        return None

    labels = _normalize_classifier_labels(item.get("labels"))
    rules = [_normalize_classifier_rule(rule) for rule in (item.get("rules") or [])]
    rules = [rule for rule in rules if rule]

    if not labels:
        inferred = []
        for rule in rules:
            label = str(rule.get("label") or "").strip()
            if label:
                inferred.append(label)
        labels = _normalize_classifier_labels(inferred)
    if not labels:
        labels = [{"id": "unknown"}]

    default_label = str(item.get("default_label") or "").strip()
    if not default_label:
        default_label = labels[0]["id"]

    out = {
        "id": cid,
        "labels": labels,
        "default_label": default_label,
        "rules": rules,
    }

    description = str(item.get("description") or "").strip()
    if description:
        out["description"] = description

    scope = item.get("scope")
    if isinstance(scope, dict):
        scope_out = {}
        if "workflow_only" in scope:
            scope_out["workflow_only"] = bool(scope.get("workflow_only"))
        stage_ids = scope.get("stage_ids")
        if isinstance(stage_ids, list):
            normalized_stage_ids = [str(value).strip() for value in stage_ids if str(value).strip()]
            if normalized_stage_ids:
                scope_out["stage_ids"] = normalized_stage_ids
        if scope_out:
            out["scope"] = scope_out

    unknown_policy = item.get("unknown_policy")
    if isinstance(unknown_policy, dict):
        unknown_out = {}
        if "on_missing_evidence" in unknown_policy:
            unknown_out["on_missing_evidence"] = bool(unknown_policy.get("on_missing_evidence"))
        if "min_confidence" in unknown_policy:
            try:
                unknown_out["min_confidence"] = float(unknown_policy.get("min_confidence"))
            except Exception:
                pass
        if unknown_out:
            out["unknown_policy"] = unknown_out

    return out


def _merge_classifier_lists(existing, additions):
    out = [item for item in (existing or []) if isinstance(item, dict) and item.get("id")]
    by_id = {item.get("id"): idx for idx, item in enumerate(out)}
    for item in additions or []:
        cid = item.get("id")
        if not cid:
            continue
        if cid in by_id:
            out[by_id[cid]] = item
        else:
            by_id[cid] = len(out)
            out.append(item)
    return out


def _deep_merge_dict(base, overlay):
    merged = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _merge_question_lists(existing, additions):
    out = [item for item in (existing or []) if isinstance(item, dict) and item.get("id")]
    by_id = {item.get("id"): idx for idx, item in enumerate(out)}
    for item in additions or []:
        qid = item.get("id")
        if not qid:
            continue
        if qid in by_id:
            out[by_id[qid]] = item
        else:
            by_id[qid] = len(out)
            out.append(item)
    return out


def _apply_overlay(base, overlay, source, warnings):
    if not isinstance(overlay, dict):
        warnings.append(f"judge overlay invalid for {source}; skipping")
        return copy.deepcopy(base)

    merged = copy.deepcopy(base)
    data = copy.deepcopy(overlay)

    if "llm" in data and isinstance(data.get("llm"), dict):
        llm_questions = data.get("llm", {}).get("questions")
        if "questions" not in data and isinstance(llm_questions, list):
            data["questions"] = llm_questions

    if "objective_weights_override" in data and "objective_weights" not in data:
        data["objective_weights"] = data.get("objective_weights_override")

    question_additions = []
    for key in ("additional_questions", "questions_extra"):
        items = data.pop(key, None)
        if isinstance(items, list):
            for item in items:
                normalized = _normalize_question(item)
                if normalized:
                    question_additions.append(normalized)

    milestones_add = _normalize_text_list(data.pop("additional_milestones", None) or data.pop("milestones_extra", None))
    anti_patterns_add = _normalize_text_list(
        data.pop("additional_anti_patterns", None) or data.pop("anti_patterns_extra", None)
    )
    prompt_notes_add = _normalize_text_list(data.pop("prompt_notes_extra", None))
    classifier_additions = []
    for key in ("additional_classifiers", "classifiers_extra"):
        items = data.pop(key, None)
        if not isinstance(items, list):
            continue
        for item in items:
            normalized = _normalize_classifier(item)
            if normalized:
                classifier_additions.append(normalized)

    for key, value in list(data.items()):
        if key == "questions":
            normalized = []
            for item in value or []:
                q = _normalize_question(item)
                if q:
                    normalized.append(q)
            if normalized:
                merged["questions"] = normalized
            else:
                warnings.append(f"judge overlay questions empty/invalid for {source}; keeping existing questions")
            continue

        if key == "objective_weights":
            merged["objective_weights"] = _normalize_objective_weights(value)
            continue

        if key in ("milestones", "anti_patterns", "prompt_notes"):
            normalized = _normalize_text_list(value)
            merged[key] = normalized
            continue

        if key == "classifiers":
            normalized = [_normalize_classifier(item) for item in (value or [])]
            normalized = [item for item in normalized if item]
            if normalized:
                merged["classifiers"] = normalized
            else:
                warnings.append(f"judge overlay classifiers empty/invalid for {source}; keeping existing classifiers")
            continue

        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged.get(key) or {}, value)
        else:
            merged[key] = copy.deepcopy(value)

    if question_additions:
        merged["questions"] = _merge_question_lists(merged.get("questions") or [], question_additions)

    if milestones_add:
        merged["milestones"] = list(dict.fromkeys((merged.get("milestones") or []) + milestones_add))

    if anti_patterns_add:
        merged["anti_patterns"] = list(dict.fromkeys((merged.get("anti_patterns") or []) + anti_patterns_add))

    if prompt_notes_add:
        merged["prompt_notes"] = list(dict.fromkeys((merged.get("prompt_notes") or []) + prompt_notes_add))

    if classifier_additions:
        merged["classifiers"] = _merge_classifier_lists(merged.get("classifiers") or [], classifier_additions)

    return merged


def _finalize_rubric(rubric, source_id, warnings):
    merged = copy.deepcopy(rubric if isinstance(rubric, dict) else DEFAULT_RUBRIC)

    rubric_id = str(merged.get("rubric_id") or merged.get("id") or source_id).strip()
    rubric_version = str(merged.get("rubric_version") or merged.get("version") or "1").strip()
    merged["rubric_id"] = rubric_id or source_id
    merged["rubric_version"] = rubric_version or "1"
    merged["objective_weights"] = _normalize_objective_weights(merged.get("objective_weights"))

    questions_raw = merged.get("questions")
    if not isinstance(questions_raw, list):
        llm = merged.get("llm") if isinstance(merged.get("llm"), dict) else {}
        questions_raw = llm.get("questions") if isinstance(llm.get("questions"), list) else []

    questions = []
    for item in questions_raw or []:
        normalized = _normalize_question(item)
        if normalized:
            questions.append(normalized)
    if not questions:
        warnings.append(f"judge rubric missing valid questions for {source_id}; using defaults")
        questions = copy.deepcopy(DEFAULT_RUBRIC["questions"])
    merged["questions"] = questions

    if isinstance(merged.get("llm"), dict):
        merged["llm"] = _deep_merge_dict(merged.get("llm") or {}, {"questions": copy.deepcopy(questions)})

    for key in ("milestones", "anti_patterns", "prompt_notes"):
        value = merged.get(key)
        if isinstance(value, list):
            merged[key] = _normalize_text_list(value)
        elif key not in merged:
            merged[key] = copy.deepcopy(DEFAULT_RUBRIC.get(key) or [])

    classifiers_raw = merged.get("classifiers")
    if not isinstance(classifiers_raw, list):
        classifiers_raw = []
    classifiers = [_normalize_classifier(item) for item in classifiers_raw]
    classifiers = [item for item in classifiers if item]
    merged["classifiers"] = classifiers

    return merged


def _load_global_base(warnings):
    path = RESOURCES_DIR / "judge_base.yaml"
    if not path.exists():
        return {}, str(path)
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        warnings.append("global judge_base.yaml invalid; ignoring")
        return {}, str(path)
    return raw, str(path)


def _load_service_base(service, warnings):
    path = RESOURCES_DIR / service / "judge_base.yaml"
    if not path.exists():
        return {}, str(path)
    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        warnings.append(f"service judge_base.yaml invalid for {service}; ignoring")
        return {}, str(path)
    return raw, str(path)


def _extract_base_defaults(base):
    if not isinstance(base, dict):
        return {}
    if isinstance(base.get("defaults"), dict):
        return copy.deepcopy(base.get("defaults") or {})

    # Backward compatibility: allow judge_base.yaml to be a direct overlay object.
    legacy_defaults = {
        key: value for key, value in base.items() if key not in _BASE_RESERVED_KEYS
    }
    return legacy_defaults


def _load_case_selection(service, case, warnings):
    test_path = RESOURCES_DIR / service / case / "test.yaml"
    case_rubric_path = RESOURCES_DIR / service / case / "judge.yaml"

    profile = None
    overrides = {}

    test_raw = _read_yaml(test_path)
    if isinstance(test_raw, dict):
        judge_block = test_raw.get("judge")
        if isinstance(judge_block, dict):
            profile_value = str(judge_block.get("profile") or "").strip()
            if profile_value:
                profile = profile_value
            direct_overlay = {
                key: value
                for key, value in judge_block.items()
                if key not in ("profile", "overrides")
            }
            overrides = _deep_merge_dict(overrides, direct_overlay)
            if isinstance(judge_block.get("overrides"), dict):
                overrides = _deep_merge_dict(overrides, judge_block.get("overrides"))

    if case_rubric_path.exists():
        case_raw = _read_yaml(case_rubric_path)
        if isinstance(case_raw, dict):
            profile_value = str(case_raw.get("profile") or "").strip()
            if profile_value and not profile:
                profile = profile_value
            direct_overlay = {
                key: value
                for key, value in case_raw.items()
                if key not in ("profile", "overrides")
            }
            overrides = _deep_merge_dict(overrides, direct_overlay)
            if isinstance(case_raw.get("overrides"), dict):
                overrides = _deep_merge_dict(overrides, case_raw.get("overrides"))
        else:
            warnings.append(f"case judge.yaml invalid for {service}/{case}; ignoring")

    return {
        "profile": profile,
        "overrides": overrides,
        "test_path": str(test_path),
        "case_rubric_path": str(case_rubric_path) if case_rubric_path.exists() else None,
    }


def _resolve_profile_overlay(profile_name, global_base, service_base):
    if not profile_name:
        return None, None
    service_profiles = service_base.get("profiles") if isinstance(service_base.get("profiles"), dict) else {}
    if profile_name in service_profiles and isinstance(service_profiles.get(profile_name), dict):
        return service_profiles.get(profile_name), "service"

    global_profiles = global_base.get("profiles") if isinstance(global_base.get("profiles"), dict) else {}
    if profile_name in global_profiles and isinstance(global_profiles.get(profile_name), dict):
        return global_profiles.get(profile_name), "global"

    return None, None


def load_merged_rubric(service, case, warnings):
    warnings = warnings if warnings is not None else []
    merged = copy.deepcopy(DEFAULT_RUBRIC)

    source_layers = []
    global_base, global_path = _load_global_base(warnings)
    global_defaults = _extract_base_defaults(global_base)
    if global_defaults:
        merged = _apply_overlay(
            merged,
            global_defaults,
            f"global defaults ({global_path})",
            warnings,
        )
        source_layers.append({"layer": "global_defaults", "path": global_path})

    service_base, service_path = _load_service_base(service, warnings)
    service_defaults = _extract_base_defaults(service_base)
    if service_defaults:
        merged = _apply_overlay(
            merged,
            service_defaults,
            f"service defaults ({service_path})",
            warnings,
        )
        source_layers.append({"layer": "service_defaults", "path": service_path})

    case_selection = _load_case_selection(service, case, warnings)
    profile_name = case_selection.get("profile")
    if profile_name:
        profile_overlay, profile_scope = _resolve_profile_overlay(profile_name, global_base, service_base)
        if profile_overlay is None:
            warnings.append(f"judge profile '{profile_name}' not found for {service}/{case}; using merged defaults")
        else:
            source_hint = "service profile" if profile_scope == "service" else "global profile"
            merged = _apply_overlay(
                merged,
                profile_overlay,
                f"{source_hint} {profile_name}",
                warnings,
            )
            source_layers.append(
                {
                    "layer": "profile",
                    "scope": profile_scope,
                    "name": profile_name,
                    "path": service_path if profile_scope == "service" else global_path,
                }
            )

    case_overrides = case_selection.get("overrides") or {}
    if case_overrides:
        merged = _apply_overlay(merged, case_overrides, f"case overrides ({service}/{case})", warnings)
        source_layers.append(
            {
                "layer": "case_overrides",
                "path": case_selection.get("case_rubric_path") or case_selection.get("test_path"),
            }
        )

    overlays = []

    source_id = f"{service}/{case}"
    merged = _finalize_rubric(merged, source_id, warnings)
    merged["source"] = {
        "case": source_id,
        "profile": profile_name,
        "layers": source_layers,
        "overlay_count": len(overlays),
        "overlays": overlays,
        "case_rubric_path": case_selection.get("case_rubric_path"),
        "test_path": case_selection.get("test_path"),
    }
    return merged
