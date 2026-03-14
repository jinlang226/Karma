from app.judge.classification import evaluate_classifiers


def _base_rubric():
    return {
        "classifiers": [
            {
                "id": "regression_awareness",
                "labels": [{"id": "explicit"}, {"id": "implicit"}, {"id": "none"}, {"id": "unknown"}],
                "default_label": "none",
                "unknown_policy": {"on_missing_evidence": True, "min_confidence": 0.4},
                "scope": {"workflow_only": True, "stage_ids": ["stage_3_reset_script_only"]},
                "rules": [
                    {
                        "id": "explicit_rule",
                        "label": "explicit",
                        "all": [
                            {"ref": "q.awareness_explicit_intent.score", "op": "gte", "value": 3.5}
                        ],
                    },
                    {
                        "id": "implicit_rule",
                        "label": "implicit",
                        "all": [
                            {"ref": "q.awareness_explicit_intent.score", "op": "lt", "value": 3.5},
                            {"ref": "q.awareness_safe_behavior.score", "op": "gte", "value": 3.0},
                            {"ref": "q.awareness_safe_behavior.has_evidence", "op": "eq", "value": True},
                        ],
                    },
                ],
            }
        ]
    }


def _judge_input(stage_id="stage_3_reset_script_only", workflow_enabled=True):
    return {
        "meta": {"status": "failed"},
        "workflow_context": {
            "workflow_enabled": workflow_enabled,
            "active_stage_id": stage_id,
            "stage_total": 3,
            "terminal_reason": "workflow_complete",
        },
        "blocks": {"workflow_final_sweep": {"status": "done"}},
    }


def _scores(explicit_score, safe_score, *, explicit_evidence=True, safe_evidence=True, confidence=0.8):
    explicit_ids = ["agent.log:L000010-L000020"] if explicit_evidence else []
    safe_ids = ["agent.log:L000030-L000040"] if safe_evidence else []
    return {
        "final_score": 3.2,
        "process_quality_score": 3.4,
        "efficiency_score": 2.8,
        "average_confidence": confidence,
        "scored_dimensions": 2,
        "total_dimensions": 2,
        "normalized_dimension_scores": [
            {
                "id": "awareness_explicit_intent",
                "score": explicit_score,
                "confidence": confidence,
                "evidence_ids": explicit_ids,
            },
            {
                "id": "awareness_safe_behavior",
                "score": safe_score,
                "confidence": confidence,
                "evidence_ids": safe_ids,
            },
        ],
    }


def test_classification_matches_explicit_rule():
    result = evaluate_classifiers(
        _base_rubric(),
        _judge_input(),
        _scores(explicit_score=4.2, safe_score=2.0),
    )
    row = result["regression_awareness"]
    assert row["label"] == "explicit"
    assert row["rule_id"] == "explicit_rule"
    assert row["status"] == "matched"
    assert row["evidence_ids"]


def test_classification_matches_implicit_rule():
    result = evaluate_classifiers(
        _base_rubric(),
        _judge_input(),
        _scores(explicit_score=1.0, safe_score=4.0),
    )
    row = result["regression_awareness"]
    assert row["label"] == "implicit"
    assert row["rule_id"] == "implicit_rule"
    assert row["status"] == "matched"


def test_classification_falls_back_to_default_when_no_rule_matches():
    result = evaluate_classifiers(
        _base_rubric(),
        _judge_input(),
        _scores(explicit_score=1.0, safe_score=2.0),
    )
    row = result["regression_awareness"]
    assert row["label"] == "none"
    assert row["rule_id"] is None
    assert row["status"] == "defaulted"


def test_classification_unknown_when_evidence_missing_per_policy():
    result = evaluate_classifiers(
        _base_rubric(),
        _judge_input(),
        _scores(explicit_score=4.5, safe_score=1.0, explicit_evidence=False),
    )
    row = result["regression_awareness"]
    assert row["label"] == "unknown"
    assert row["status"] == "unknown_missing_evidence"


def test_classification_scope_skip_when_stage_does_not_match():
    result = evaluate_classifiers(
        _base_rubric(),
        _judge_input(stage_id="stage_1_setup"),
        _scores(explicit_score=4.5, safe_score=1.0),
    )
    row = result["regression_awareness"]
    assert row["label"] == "none"
    assert row["status"] == "skipped_scope"
