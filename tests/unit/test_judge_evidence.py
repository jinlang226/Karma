from app.judge.evidence import validate_evidence_ids


def test_validate_evidence_ids_counts_and_reasons():
    judge_input = {
        "blocks": {
            "agent_log": {"line_count": 5},
            "external_metrics": {"read_write_ratio": {"total_commands": 99}},
            "agent_usage": {"totals": {"total_tokens": 12345}},
            "efficiency_facts": {"time_to_success_seconds": 629},
        }
    }
    payload = [
        {
            "id": "q1",
            "evidence_ids": [
                "agent.log:L000001-L000002",
                "agent.log:1-2",
                "agent.log:L000010-L000020",
                "external_metrics:read_write_ratio.total_commands",
                "agent_usage:totals.total_tokens",
                "efficiency_facts:time_to_success_seconds",
                123,
            ],
        },
        {
            "id": "q2",
            "evidence_ids": [
                "agent.log:L000004-L000003",
                "",
                "external_metrics:read_write_ratio.missing_field",
                "foo:bar",
            ],
        },
    ]
    result = validate_evidence_ids(payload, judge_input=judge_input)

    assert result["valid_count"] == 4
    assert result["invalid_count"] == 6
    assert result["unvalidated_count"] == 1
    reasons = {item["reason"] for item in result["invalid"]}
    assert "bad_format" in reasons
    assert "out_of_range" in reasons
    assert "reversed_range" in reasons
    assert "non_string" in reasons
    assert "empty" in reasons
    assert "missing_path" in reasons


def test_validate_evidence_ids_accepts_non_padded_digits():
    judge_input = {"blocks": {"agent_log": {"line_count": 2}}}
    payload = [
        {
            "id": "q1",
            "evidence_ids": ["agent.log:L1-L2", "agent.log:L2-L2"],
        }
    ]
    result = validate_evidence_ids(payload, judge_input=judge_input)
    assert result["valid_count"] == 2
    assert result["invalid_count"] == 0


def test_validate_evidence_ids_flags_missing_source():
    judge_input = {"blocks": {"agent_log": {"line_count": 2}}}
    payload = [{"id": "q1", "evidence_ids": ["agent_usage:totals.total_tokens"]}]
    result = validate_evidence_ids(payload, judge_input=judge_input)
    assert result["valid_count"] == 0
    assert result["invalid_count"] == 1
    assert result["invalid"][0]["reason"] == "missing_source"


def test_validate_evidence_ids_accepts_workflow_and_workflow_efficiency_refs():
    judge_input = {
        "workflow_context": {
            "stage_results": {"stage_a": {"status": "passed"}},
            "final_sweep": {"regression": {"stage_a": {"classification": "stable"}}},
        },
        "blocks": {
            "agent_log": {"line_count": 1},
            "workflow_efficiency_facts": {"total_stage_attempts": 3},
        },
    }
    payload = [
        {
            "id": "q1",
            "evidence_ids": [
                "workflow:stage_results.stage_a.status",
                "workflow:final_sweep.regression.stage_a.classification",
                "workflow_efficiency_facts:total_stage_attempts",
            ],
        }
    ]
    result = validate_evidence_ids(payload, judge_input=judge_input)
    assert result["valid_count"] == 3
    assert result["invalid_count"] == 0
