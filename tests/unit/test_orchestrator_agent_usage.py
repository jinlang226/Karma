import json
import shutil

from app.orchestrator_core.artifacts import ingest_agent_usage
from app.settings import ROOT, RUNS_DIR


def test_ingest_agent_usage_updates_run_artifacts():
    run_root = RUNS_DIR / "unit-agent-usage-ingest"
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)

    (run_root / "meta.json").write_text(
        json.dumps({"service": "svc", "case": "case", "run_dir": str(run_root.relative_to(ROOT))}, indent=2),
        encoding="utf-8",
    )
    (run_root / "external_metrics.json").write_text(json.dumps({"existing": 1}, indent=2), encoding="utf-8")
    (run_root / "agent_usage_raw.json").write_text(
        json.dumps(
            {
                "provider": "codex",
                "source": "codex_session_store",
                "available": True,
                "totals": {
                    "input_tokens": 120,
                    "cached_input_tokens": 80,
                    "output_tokens": 30,
                    "reasoning_output_tokens": 10,
                    "total_tokens": 150,
                },
                "model_breakdown": {"gpt-5.2-codex": {"total_tokens": 150, "count": 3}},
                "events_count": 3,
                "files_scanned": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    usage = ingest_agent_usage(run_root, root=ROOT)
    assert usage is not None
    assert usage.get("token_usage_available") is True
    assert usage.get("token_usage_total_tokens") == 150
    assert usage.get("agent_usage_path")

    usage_path = ROOT / usage["agent_usage_path"]
    assert usage_path.exists()
    usage_payload = json.loads(usage_path.read_text(encoding="utf-8"))
    assert usage_payload.get("schema_version") == "agent_usage.v1"
    assert (usage_payload.get("totals") or {}).get("total_tokens") == 150

    metrics_payload = json.loads((run_root / "external_metrics.json").read_text(encoding="utf-8"))
    assert metrics_payload.get("existing") == 1
    assert (metrics_payload.get("agent_token_usage") or {}).get("available") is True

    meta_payload = json.loads((run_root / "meta.json").read_text(encoding="utf-8"))
    assert meta_payload.get("token_usage_total_tokens") == 150
    assert meta_payload.get("agent_usage_path") == usage.get("agent_usage_path")
