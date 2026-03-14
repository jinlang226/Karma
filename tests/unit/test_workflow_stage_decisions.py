from app.orchestrator_core.workflow_run import (
    workflow_status_from_stage,
    workflow_submit_payload,
)


def test_workflow_status_from_stage_classification_matrix():
    status, reason = workflow_status_from_stage(
        {"status": "passed", "last_verification_kind": ""}
    )
    assert status == "passed"
    assert reason == "stage_passed"

    status, reason = workflow_status_from_stage(
        {"status": "failed", "last_verification_kind": "oracle_failed"}
    )
    assert status == "failed"
    assert reason == "oracle_failed"

    status, reason = workflow_status_from_stage(
        {"status": "auto_failed", "last_verification_kind": "oracle_timeout"}
    )
    assert status == "fatal_error"
    assert reason == "oracle_timeout"

    status, reason = workflow_status_from_stage(
        {"status": "setup_failed", "last_verification_kind": "oracle_harness_error"}
    )
    assert status == "fatal_error"
    assert reason == "oracle_harness_error"


def test_workflow_submit_payload_required_fields_shape():
    payload = workflow_submit_payload(
        base_status="failed",
        attempt=2,
        last_error="verify failed",
        verification_log="runs/x/verification_2.log",
        attempts_left=1,
        time_left_sec=120,
        can_retry=True,
        mode="progressive",
        stage_index=2,
        stage_total=4,
        stage_id="stage_scale",
        stage_attempt=2,
        stage_status="failed_retryable",
        continue_flag=False,
        final_flag=False,
        next_stage_id=None,
        reason="oracle_failed_retryable",
    )
    assert payload["status"] == "failed"
    assert payload["can_retry"] is True
    wf = payload.get("workflow") or {}
    required = {
        "enabled",
        "mode",
        "stage_index",
        "stage_total",
        "stage_id",
        "stage_attempt",
        "stage_status",
        "continue",
        "final",
        "next_stage_id",
        "reason",
    }
    assert required.issubset(set(wf.keys()))
    assert wf["enabled"] is True
    assert wf["stage_id"] == "stage_scale"
    assert wf["stage_status"] == "failed_retryable"
    assert wf["continue"] is False
    assert wf["final"] is False
