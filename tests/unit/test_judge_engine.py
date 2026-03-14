import json
import shutil

from app.judge.engine import TrajectoryJudge
from app.settings import ROOT, RUNS_DIR


def _make_run_dir(name):
    run_root = RUNS_DIR / name
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    meta = {
        "service": "rabbitmq-experiments",
        "case": "manual_monitoring",
        "test_file": "test.yaml",
        "status": "failed",
        "attempts": 1,
        "max_attempts": 3,
        "verification_logs": [],
        "setup_started_at_ts": 1,
        "setup_finished_at_ts": 2,
        "solve_started_at_ts": 3,
        "finished_at_ts": 10,
        "solve_pause_total_sec": 0,
    }
    (run_root / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (run_root / "agent.log").write_text("[agent] thinking\n[agent] exec\n", encoding="utf-8")
    return run_root


def test_engine_success_writes_result_and_meta_pointer():
    run_root = _make_run_dir("unit-judge-engine-success")
    judge = TrajectoryJudge(
        base_url="http://127.0.0.1:1/v1",
        api_key="dummy",
        model="dummy-model",
        fail_open=True,
    )

    response = {
        "dimension_scores": [
            {"id": "diagnosis_speed", "score": 4, "confidence": 0.9, "evidence_ids": ["E001"], "rationale": "ok"},
            {"id": "hypothesis_quality", "score": 4, "confidence": 0.9, "evidence_ids": ["E001"], "rationale": "ok"},
            {"id": "debugging_discipline", "score": 4, "confidence": 0.9, "evidence_ids": ["E001"], "rationale": "ok"},
            {"id": "fix_robustness", "score": 4, "confidence": 0.9, "evidence_ids": ["E001"], "rationale": "ok"},
            {"id": "resource_efficiency", "score": 3, "confidence": 0.9, "evidence_ids": ["E001"], "rationale": "ok"},
        ],
        "milestone_coverage": {"covered": [], "missed": []},
        "anti_pattern_flags": [],
        "overall_assessment": "good",
        "limitations": [],
    }

    calls = {"count": 0}

    def _mock_create(_messages):
        calls["count"] += 1
        return {
            "raw_response": {"mock": True, "call": calls["count"]},
            "content": json.dumps(response),
        }

    judge.client.create_judgement = _mock_create

    summary = judge.evaluate_run(run_root)
    assert summary["judge_status"] == "ok"
    assert summary["final_score"] is not None

    result_path = ROOT / summary["result_path"]
    assert result_path.exists()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert "evidence_validation" in result
    assert "classifications" in result
    assert isinstance(result.get("classifications"), dict)
    assert "agent.log" in result["evidence_validation"]["validated_scope"]

    meta = json.loads((run_root / "meta.json").read_text(encoding="utf-8"))
    assert meta.get("judge_status") == "ok"
    assert meta.get("judge_path")


def test_engine_fail_open_returns_error_result():
    run_root = _make_run_dir("unit-judge-engine-fail-open")
    judge = TrajectoryJudge(
        base_url="http://127.0.0.1:1/v1",
        api_key="dummy",
        model="dummy-model",
        fail_open=True,
    )

    def _mock(_messages):
        raise RuntimeError("boom")

    judge.client.create_judgement = _mock
    summary = judge.evaluate_run(run_root)

    assert summary["judge_status"] == "error"
    assert "boom" in (summary.get("error") or "")
    result = json.loads((run_root / "judge" / "result_v1.json").read_text(encoding="utf-8"))
    assert result.get("judge_status") == "error"


def test_engine_dry_run_writes_prompt_without_llm_call():
    run_root = _make_run_dir("unit-judge-engine-dry-run")
    stale_dir = run_root / "judge" / "chunks"
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "stale.txt").write_text("stale", encoding="utf-8")

    judge = TrajectoryJudge(
        base_url="http://127.0.0.1:1/v1",
        api_key="dummy",
        model="dummy-model",
        fail_open=True,
        dry_run=True,
    )

    summary = judge.evaluate_run(run_root)

    assert summary["judge_status"] == "dry_run"
    assert summary["final_score"] is None
    assert summary.get("prompt_path")
    assert summary.get("input_path")
    assert (ROOT / summary["prompt_path"]).exists()
    assert (ROOT / summary["input_path"]).exists()
    prompt_payload = json.loads((ROOT / summary["prompt_path"]).read_text(encoding="utf-8"))
    user_payload = json.loads(prompt_payload[1]["content"])
    prompt_agent_log = (((user_payload or {}).get("judge_input") or {}).get("blocks") or {}).get("agent_log") or {}
    assert "text" not in prompt_agent_log
    assert "text_numbered" in prompt_agent_log
    raw_input_payload = json.loads((ROOT / summary["input_path"]).read_text(encoding="utf-8"))
    input_agent_log = (((raw_input_payload or {}).get("blocks") or {}).get("agent_log") or {})
    assert "text" in input_agent_log
    assert not (stale_dir / "stale.txt").exists()
    assert not (run_root / "judge" / "raw_response.json").exists()
    meta = json.loads((run_root / "meta.json").read_text(encoding="utf-8"))
    assert meta.get("judge_status") is None


def test_engine_dry_run_includes_workflow_prompt_instructions():
    run_root = _make_run_dir("unit-judge-engine-workflow-dry-run")
    (run_root / "workflow_state.json").write_text(
        json.dumps(
            {
                "workflow_name": "wf-demo",
                "prompt_mode": "progressive",
                "stage_total": 1,
                "active_stage_id": "stage_seed",
                "active_stage_index": 1,
                "terminal": True,
                "terminal_reason": "workflow_complete",
                "solve_status": "passed",
            }
        ),
        encoding="utf-8",
    )
    (run_root / "workflow_stage_results.jsonl").write_text(
        json.dumps(
            {
                "stage_id": "stage_seed",
                "attempt": 1,
                "status": "passed",
                "reason": "stage_passed",
                "run_dir": str(run_root),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    judge = TrajectoryJudge(
        base_url="http://127.0.0.1:1/v1",
        api_key="dummy",
        model="dummy-model",
        fail_open=True,
        dry_run=True,
    )
    summary = judge.evaluate_run(run_root)
    assert summary["judge_status"] == "dry_run"

    prompt_payload = json.loads((ROOT / summary["prompt_path"]).read_text(encoding="utf-8"))
    user_payload = json.loads(prompt_payload[1]["content"])
    assert "workflow_instructions" in user_payload


def test_engine_infers_service_case_from_workflow_stage_runs():
    run_root = RUNS_DIR / "unit-judge-engine-workflow-infer"
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "agent.log").write_text("[agent] workflow\n", encoding="utf-8")
    # Intentionally omit root meta.json service/case.
    stage_run_root = RUNS_DIR / "unit-judge-engine-workflow-infer-stage"
    shutil.rmtree(stage_run_root, ignore_errors=True)
    stage_run_root.mkdir(parents=True, exist_ok=True)
    stage_run_rel = str(stage_run_root.relative_to(ROOT))
    (run_root / "workflow_stage_results.jsonl").write_text(
        json.dumps(
            {
                "stage_id": "stage_seed",
                "attempt": 1,
                "status": "passed",
                "reason": "stage_passed",
                "run_dir": stage_run_rel,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (stage_run_root / "meta.json").write_text(
        json.dumps({"service": "rabbitmq-experiments", "case": "manual_monitoring"}),
        encoding="utf-8",
    )

    judge = TrajectoryJudge(
        base_url="http://127.0.0.1:1/v1",
        api_key="dummy",
        model="dummy-model",
        fail_open=True,
        dry_run=True,
    )
    summary = judge.evaluate_run(run_root)
    assert summary["judge_status"] == "dry_run"
    assert summary["service"] == "rabbitmq-experiments"
    assert summary["case"] == "manual_monitoring"


def test_from_args_prefers_loaded_llm_env_when_flags_missing():
    class Args:
        judge_base_url = None
        judge_api_key = None
        judge_model = None
        judge_timeout = 90
        judge_max_retries = 1
        judge_prompt_version = "v1"
        judge_fail_open = True
        judge_include_outcome = False
        dry_run = True
        _llm_env = {
            "LLM_BASE_URL": "https://example.com/v1",
            "LLM_API_KEY": "k-llm",
            "LLM_MODEL": "openai/gpt-4o-mini",
        }

    judge = TrajectoryJudge.from_args(Args())
    assert judge.base_url == "https://example.com/v1"
    assert judge.api_key == "k-llm"
    assert judge.model == "openai/gpt-4o-mini"
    assert judge.timeout_sec == 90
    assert judge.dry_run is True
