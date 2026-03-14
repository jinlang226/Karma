import importlib.util
import json
import os
import shutil
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.settings import ROOT, RUNS_DIR


class _JudgeErrorHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps({"error": {"message": "forced failure", "code": 500}}).encode("utf-8")
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _parse_json_from_stdout(stdout):
    start = stdout.find("{\n")
    if start < 0:
        start = stdout.find("{")
    if start < 0:
        raise AssertionError(stdout)
    return json.loads(stdout[start:])


def test_judge_run_fail_open_writes_error_artifacts():
    if importlib.util.find_spec("openai") is None:
        return

    run_root = RUNS_DIR / f"it_judge_fail_open_{uuid.uuid4().hex[:8]}"
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)

    (run_root / "meta.json").write_text(
        json.dumps(
            {
                "service": "rabbitmq-experiments",
                "case": "manual_monitoring",
                "status": "passed",
                "attempts": 1,
                "max_attempts": 3,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_root / "agent.log").write_text("[agent] thinking\n[agent] exec\n", encoding="utf-8")
    (run_root / "external_metrics.json").write_text(
        json.dumps({"time_to_success_seconds": 10, "read_write_ratio": {"total_commands": 3}}, indent=2),
        encoding="utf-8",
    )
    (run_root / "agent_usage.json").write_text(
        json.dumps({"totals": {"total_tokens": 100, "input_tokens": 80, "output_tokens": 20}}, indent=2),
        encoding="utf-8",
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), _JudgeErrorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    env_file = ROOT / ".benchmark" / f"it_judge_{uuid.uuid4().hex[:8]}.env"
    env_file.write_text(
        "\n".join(
            [
                f"JUDGE_BASE_URL=http://127.0.0.1:{port}/v1",
                "JUDGE_MODEL=dummy-model",
                "JUDGE_API_KEY=dummy-key",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    cmd = [
        "python3",
        "scripts/judge.py",
        "run",
        "--judge-env-file",
        str(env_file),
        "--run-dir",
        str(run_root),
        "--judge-fail-open",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=dict(os.environ),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout
        payload = _parse_json_from_stdout(proc.stdout)
        assert payload.get("judge_status") == "error"
        warnings = payload.get("warnings") or []
        assert any("judge evaluation failed" in item for item in warnings)
        result_path = ROOT / payload.get("result_path")
        assert result_path.exists()
        result_payload = json.loads(result_path.read_text(encoding="utf-8"))
        assert result_payload.get("judge_status") == "error"
        assert "forced failure" in (result_payload.get("error") or "")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)
        env_file.unlink(missing_ok=True)
        shutil.rmtree(run_root, ignore_errors=True)
