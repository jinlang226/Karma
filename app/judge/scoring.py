def _to_score(value):
    try:
        num = float(value)
    except Exception:
        return None
    if num < 0:
        num = 0.0
    if num > 5:
        num = 5.0
    return num


def _to_confidence(value):
    try:
        num = float(value)
    except Exception:
        return 0.5
    if num < 0:
        num = 0.0
    if num > 1:
        num = 1.0
    return num


def _weighted_avg(items):
    total_weight = 0.0
    total_value = 0.0
    for value, weight in items:
        total_weight += weight
        total_value += value * weight
    if total_weight <= 0:
        return None
    return total_value / total_weight


def compute_weighted_scores(rubric, model_output):
    questions = rubric.get("questions") or []
    by_id = {}
    for item in model_output.get("dimension_scores") or []:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "").strip()
        if not qid:
            continue
        by_id[qid] = item

    normalized = []
    process_values = []
    efficiency_values = []

    for q in questions:
        qid = q.get("id")
        track = q.get("track") or "process_quality"
        weight = float(q.get("weight") or 1.0)
        row = by_id.get(qid, {})
        score = _to_score(row.get("score"))
        confidence = _to_confidence(row.get("confidence"))
        evidence_ids = row.get("evidence_ids") if isinstance(row.get("evidence_ids"), list) else []
        rationale = str(row.get("rationale") or row.get("reason") or "").strip()

        normalized.append(
            {
                "id": qid,
                "track": track,
                "weight": weight,
                "score": score,
                "confidence": confidence,
                "evidence_ids": evidence_ids,
                "rationale": rationale,
            }
        )

        if score is None:
            continue
        if track == "efficiency":
            efficiency_values.append((score, weight))
        else:
            process_values.append((score, weight))

    process_quality = _weighted_avg(process_values)
    efficiency = _weighted_avg(efficiency_values)

    weights = rubric.get("objective_weights") or {}
    process_w = float(weights.get("process_quality", 0.7) or 0.7)
    efficiency_w = float(weights.get("efficiency", 0.3) or 0.3)
    total = process_w + efficiency_w
    if total <= 0:
        process_w, efficiency_w = 0.7, 0.3
        total = 1.0
    process_w = process_w / total
    efficiency_w = efficiency_w / total

    configured_weights = {
        "process_quality": process_w,
        "efficiency": efficiency_w,
    }

    missing_tracks = []
    applied_process_w = process_w
    applied_efficiency_w = efficiency_w
    if process_quality is None:
        missing_tracks.append("process_quality")
        applied_process_w = 0.0
    if efficiency is None:
        missing_tracks.append("efficiency")
        applied_efficiency_w = 0.0

    available_total_w = applied_process_w + applied_efficiency_w
    if available_total_w > 0:
        applied_process_w /= available_total_w
        applied_efficiency_w /= available_total_w
        final_score = 0.0
        if process_quality is not None:
            final_score += process_quality * applied_process_w
        if efficiency is not None:
            final_score += efficiency * applied_efficiency_w
    else:
        final_score = 0.0

    scored_count = sum(1 for item in normalized if item.get("score") is not None)
    confidence_avg = _weighted_avg(
        [(item.get("confidence") or 0.0, item.get("weight") or 1.0) for item in normalized]
    )
    if confidence_avg is None:
        confidence_avg = 0.0

    return {
        "normalized_dimension_scores": normalized,
        "process_quality_score": round(process_quality, 4) if process_quality is not None else None,
        "efficiency_score": round(efficiency, 4) if efficiency is not None else None,
        "final_score": round(final_score, 4),
        "scored_dimensions": scored_count,
        "total_dimensions": len(normalized),
        "average_confidence": round(confidence_avg, 4),
        "missing_tracks": missing_tracks,
        "weights": {
            "process_quality": round(applied_process_w, 4),
            "efficiency": round(applied_efficiency_w, 4),
        },
        "weights_configured": {
            "process_quality": round(configured_weights["process_quality"], 4),
            "efficiency": round(configured_weights["efficiency"], 4),
        },
    }
