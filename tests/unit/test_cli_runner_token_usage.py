import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from app.settings import ROOT


def _run_collector(codex_home, out_path):
    cmd = [
        "python3",
        str(ROOT / "agent_tests" / "cli-runner" / "collect_token_usage.py"),
        "--codex-home",
        str(codex_home),
        "--out",
        str(out_path),
    ]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout
    return json.loads(out_path.read_text(encoding="utf-8"))


def test_collect_token_usage_from_codex_sessions():
    with TemporaryDirectory() as temp_dir:
        codex_home = Path(temp_dir) / ".codex"
        session_file = codex_home / "sessions" / "2026" / "02" / "17" / "s1.jsonl"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            {"type": "turn_context", "payload": {"model": "gpt-5.2-codex"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 20,
                            "cached_input_tokens": 5,
                            "output_tokens": 3,
                            "reasoning_output_tokens": 1,
                            "total_tokens": 23,
                        }
                    },
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 35,
                            "cached_input_tokens": 8,
                            "output_tokens": 7,
                            "reasoning_output_tokens": 2,
                            "total_tokens": 42,
                        }
                    },
                },
            },
        ]
        session_file.write_text("\n".join(json.dumps(item) for item in lines), encoding="utf-8")

        out_path = Path(temp_dir) / "usage.json"
        payload = _run_collector(codex_home, out_path)

        assert payload.get("available") is True
        totals = payload.get("totals") or {}
        assert totals.get("input_tokens") == 35
        assert totals.get("cached_input_tokens") == 8
        assert totals.get("output_tokens") == 7
        assert totals.get("reasoning_output_tokens") == 2
        assert totals.get("total_tokens") == 42
        model_breakdown = payload.get("model_breakdown") or {}
        assert "gpt-5.2-codex" in model_breakdown
        assert (model_breakdown["gpt-5.2-codex"] or {}).get("count") == 2


def test_collect_token_usage_handles_missing_sessions():
    with TemporaryDirectory() as temp_dir:
        codex_home = Path(temp_dir) / ".codex"
        out_path = Path(temp_dir) / "usage.json"
        payload = _run_collector(codex_home, out_path)
        assert payload.get("available") is False
        assert payload.get("events_count") == 0
        warnings = payload.get("warnings") or []
        assert any("no session files" in item for item in warnings)

