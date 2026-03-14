from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

from app.runner import BenchmarkApp
from app.settings import ROOT
from app.util import encode_case_id


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def test_get_case_includes_param_definitions_for_real_case():
    app = _make_app()
    case_id = encode_case_id("demo", "configmap-update", "test.yaml")
    details = app.get_case(case_id)
    assert "error" not in details
    params = details.get("params") or {}
    definitions = params.get("definitions") or {}
    assert "target_value" in definitions
    assert definitions["target_value"]["type"] == "string"
    assert definitions["target_value"]["default"] == "x"


def test_get_case_normalizes_scalar_and_typed_param_specs():
    app = _make_app()
    case_id = encode_case_id("demo", "configmap-update", "test.yaml")
    benchmark_tmp = ROOT / ".benchmark"
    benchmark_tmp.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=str(benchmark_tmp)) as td:
        root = Path(td)
        case_dir = root / "configmap_case"
        resource_dir = case_dir / "resource"
        resource_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "unit-param-provider",
            "targetApp": "demo",
            "numAppInstance": 0,
            "params": {
                "definitions": {
                    "scalar_default": "seed",
                    "replicas": {"type": "int", "default": 3, "min": 1, "max": 7},
                    "mode": {
                        "type": "enum",
                        "values": ["a", "b"],
                        "required": True,
                        "description": "enum mode",
                    },
                }
            },
        }
        case_path = case_dir / "test.yaml"
        case_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        app.set_case_path_override("demo", "configmap-update", "test.yaml", str(case_path))
        try:
            details = app.get_case(case_id)
            assert "error" not in details
            definitions = ((details.get("params") or {}).get("definitions") or {})
            assert definitions["scalar_default"] == {"type": "string", "default": "seed"}
            assert definitions["replicas"] == {"type": "int", "default": 3, "min": 1, "max": 7}
            assert definitions["mode"] == {
                "type": "enum",
                "values": ["a", "b"],
                "required": True,
                "description": "enum mode",
            }
        finally:
            app.clear_case_path_overrides()
