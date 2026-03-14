import json
import shutil

from app.judge.input_builder import JudgeInputBuilder
from app.settings import ROOT, RUNS_DIR


def test_builder_collects_timeline_and_blinds_outcome():
    run_root = RUNS_DIR / "unit-judge-input"
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)

    (run_root / "agent.log").write_text(
        "[agent] thinking\n[agent] exec\n[agent] publish failed\n",
        encoding="utf-8",
    )
    (run_root / "agent_usage.json").write_text(
        json.dumps({"total_tokens": 123, "input_tokens": 100, "output_tokens": 23}),
        encoding="utf-8",
    )
    (run_root / "external_metrics.json").write_text(
        json.dumps({"duration_sec": 33, "agent_token_usage": {"total_tokens": 123}}),
        encoding="utf-8",
    )

    meta = {
        "service": "rabbitmq-experiments",
        "case": "manual_monitoring",
        "test_file": "test.yaml",
        "status": "passed",
        "attempts": 1,
        "max_attempts": 3,
        "setup_log": None,
        "verification_logs": [],
        "cleanup_log": None,
        "solve_started_at_ts": 100,
        "finished_at_ts": 130,
        "setup_started_at_ts": 10,
        "setup_finished_at_ts": 20,
        "solve_pause_total_sec": 5,
    }
    (run_root / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    rubric = {
        "rubric_id": "r1",
        "rubric_version": "1",
        "questions": [{"id": "diagnosis_speed", "track": "process_quality", "weight": 1.0, "prompt": "x"}],
    }
    builder = JudgeInputBuilder(include_outcome=False)
    packet, context = builder.build(run_root, rubric)

    assert "status" not in packet["meta"]
    assert packet["blocks"]["agent_log"]["line_count"] == 3
    assert packet["blocks"]["agent_log"]["text_numbered"].startswith("L000001 [agent] thinking")
    assert "L000003 [agent] publish failed" in packet["blocks"]["agent_log"]["text_numbered"]
    assert packet["blocks"]["efficiency_facts"]["total_tokens"] == 123
    assert packet["blocks"]["efficiency_facts"]["solve_duration_sec"] == 30
    assert packet["blocks"]["external_metrics"]["duration_sec"] == 33
    assert packet["blocks"]["agent_usage"]["total_tokens"] == 123
    assert packet["blocks"]["agent_usage"]["totals"]["total_tokens"] == 123
    assert packet["workflow_context"]["workflow_enabled"] is False
    assert packet["workflow_context"]["stage_total"] == 1
    assert packet["blocks"]["workflow_stage_results"]["manual_monitoring"]["status"] == "passed"
    assert packet["blocks"]["workflow_efficiency_facts"]["total_stage_attempts"] == 1
    assert packet["objective_metrics"]["derived_signals"]["agent_log_line_count"] == 3
    assert packet["case_context"]["problem_statement"]
    assert context["service"] == "rabbitmq-experiments"
    assert context["case"] == "manual_monitoring"


def test_builder_collects_workflow_context_from_workflow_files():
    run_root = RUNS_DIR / "unit-judge-input-workflow"
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)

    (run_root / "agent.log").write_text("[agent] workflow\n", encoding="utf-8")
    (run_root / "workflow_state.json").write_text(
        json.dumps(
            {
                "workflow_name": "wf-demo",
                "prompt_mode": "progressive",
                "stage_total": 2,
                "active_stage_id": "stage_b",
                "active_stage_index": 2,
                "terminal": True,
                "terminal_reason": "workflow_complete",
                "solve_status": "passed",
            }
        ),
        encoding="utf-8",
    )
    stage_a_root = RUNS_DIR / "unit-judge-input-workflow-stage-a"
    stage_b_root = RUNS_DIR / "unit-judge-input-workflow-stage-b"
    shutil.rmtree(stage_a_root, ignore_errors=True)
    shutil.rmtree(stage_b_root, ignore_errors=True)
    stage_a_root.mkdir(parents=True, exist_ok=True)
    stage_b_root.mkdir(parents=True, exist_ok=True)
    stage_a_rel = str(stage_a_root.relative_to(ROOT))
    stage_b_rel = str(stage_b_root.relative_to(ROOT))

    (run_root / "workflow_stage_results.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "stage_id": "stage_a",
                        "attempt": 1,
                        "status": "passed",
                        "reason": "stage_passed",
                        "run_dir": stage_a_rel,
                    }
                ),
                json.dumps(
                    {
                        "stage_id": "stage_b",
                        "attempt": 2,
                        "status": "failed_exhausted",
                        "reason": "oracle_failed",
                        "run_dir": stage_b_rel,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (run_root / "submit_results.log").write_text(
        json.dumps({"workflow": {"stage_id": "stage_a", "continue": True}}) + "\n",
        encoding="utf-8",
    )
    (run_root / "workflow_final_sweep.json").write_text(
        json.dumps(
            {
                "regression": {
                    "stage_a": {"classification": "stable"},
                    "stage_b": {"classification": "unexpected_regression"},
                }
            }
        ),
        encoding="utf-8",
    )

    (stage_a_root / "meta.json").write_text(
        json.dumps(
            {
                "service": "workflow-mock",
                "case": "stage_seed",
                "setup_started_at_ts": 10,
                "setup_finished_at_ts": 20,
                "solve_started_at_ts": 21,
                "finished_at_ts": 31,
            }
        ),
        encoding="utf-8",
    )
    (stage_b_root / "meta.json").write_text(
        json.dumps(
            {
                "service": "workflow-mock",
                "case": "stage_finalize",
                "setup_started_at_ts": 40,
                "setup_finished_at_ts": 52,
                "solve_started_at_ts": 53,
                "finished_at_ts": 77,
            }
        ),
        encoding="utf-8",
    )

    rubric = {
        "rubric_id": "r1",
        "rubric_version": "1",
        "questions": [{"id": "diagnosis_speed", "track": "process_quality", "weight": 1.0, "prompt": "x"}],
    }
    builder = JudgeInputBuilder(include_outcome=False)
    packet, context = builder.build(run_root, rubric)

    wf = packet["workflow_context"]
    assert wf["workflow_enabled"] is True
    assert wf["workflow_id"] == "wf-demo"
    assert wf["stage_total"] == 2
    assert wf["stage_results"]["stage_b"]["attempts"] == 2
    assert packet["blocks"]["workflow_submit_results"]["event_001"]["workflow"]["stage_id"] == "stage_a"
    assert (
        packet["blocks"]["workflow_final_sweep"]["regression"]["stage_b"]["classification"]
        == "unexpected_regression"
    )
    assert packet["blocks"]["workflow_efficiency_facts"]["total_stage_attempts"] == 3
    assert context["service"] == "workflow-mock"
    assert context["case"] == "stage_seed"
