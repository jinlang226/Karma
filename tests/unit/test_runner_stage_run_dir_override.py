from pathlib import Path
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.settings import ROOT
from app.util import encode_case_id


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def _minimal_case_data():
    return {
        "type": "unit",
        "targetApp": "unit",
        "numAppInstance": 1,
        "preconditionUnits": [],
        "oracle": {"verify": {"commands": [{"command": ["/bin/true"]}]}},
    }


def test_start_run_honors_next_run_dir_override_inside_repo():
    app = _make_app()
    app._run_setup = lambda: None

    case_id = encode_case_id("rabbitmq-experiments", "manual_monitoring", "test.yaml")
    override_dir = ROOT / ".benchmark" / "unit_stage_runs" / "wf_a" / "stage_runs" / "01_stage_1"
    app._next_run_dir_override = str(override_dir)

    out = app.start_run(
        case_id,
        defer_cleanup=True,
        case_data_override=_minimal_case_data(),
        namespace_context={"default_role": "default", "roles": {"default": "ns-a"}},
    )

    assert out.get("status") == "started"
    resolved = (ROOT / app.run_state.get("run_dir", "")).resolve()
    assert resolved == override_dir.resolve()
    assert resolved.is_dir()


def test_start_run_rejects_next_run_dir_override_outside_repo():
    app = _make_app()
    case_id = encode_case_id("rabbitmq-experiments", "manual_monitoring", "test.yaml")
    app._next_run_dir_override = "/tmp/outside-benchmark-run"

    out = app.start_run(
        case_id,
        defer_cleanup=True,
        case_data_override=_minimal_case_data(),
        namespace_context={"default_role": "default", "roles": {"default": "ns-a"}},
    )

    assert "run_dir_override must resolve inside repository root" in str(out.get("error") or "")
