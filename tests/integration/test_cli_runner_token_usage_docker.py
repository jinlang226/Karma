import json
import shutil
import uuid

from app.orchestrator_core.artifacts import attach_agent_usage_fields
from app.settings import ROOT, RUNS_DIR


def test_attach_agent_usage_fields_ingests_raw_usage_payload():
    run_root = RUNS_DIR / f"it_cli_runner_usage_{uuid.uuid4().hex[:8]}"
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)

    (run_root / "meta.json").write_text(
        json.dumps(
            {
                "service": "rabbitmq-experiments",
                "case": "manual_monitoring",
                "run_dir": str(run_root.relative_to(ROOT)),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_root / "external_metrics.json").write_text(json.dumps({"existing": 1}, indent=2), encoding="utf-8")
    (run_root / "agent_usage_raw.json").write_text(
        json.dumps(
            {
                "provider": "codex-cli",
                "source": "codex_session_store",
                "available": True,
                "totals": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 300,
                    "output_tokens": 200,
                    "reasoning_output_tokens": 50,
                    "total_tokens": 1200,
                },
                "model_breakdown": {"gpt-5.3-codex": {"total_tokens": 1200, "count": 10}},
                "events_count": 10,
                "files_scanned": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    try:
        outcome = {
            "status": "passed",
            "run_dir": str(run_root.relative_to(ROOT)),
        }
        merged = attach_agent_usage_fields(outcome, root=ROOT)
        assert merged["token_usage_available"] is True
        assert merged["token_usage_total_tokens"] == 1200
        assert merged["token_usage_input_tokens"] == 1000
        assert merged["token_usage_output_tokens"] == 200
        assert merged.get("agent_usage_path")
        assert merged.get("metrics_path")

        metrics = json.loads((run_root / "external_metrics.json").read_text(encoding="utf-8"))
        token_usage = metrics.get("agent_token_usage") or {}
        assert token_usage.get("available") is True
        assert (token_usage.get("totals") or {}).get("total_tokens") == 1200
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
