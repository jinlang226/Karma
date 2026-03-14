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


class _FlakyJudgeHandler(BaseHTTPRequestHandler):
    call_count = 0

    def log_message(self, _format, *_args):
        return

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        _FlakyJudgeHandler.call_count += 1
        if _FlakyJudgeHandler.call_count == 2:
            payload = json.dumps({"error": {"message": "forced failure", "code": 500}}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        content = json.dumps(
            {
                "dimension_scores": [],
                "milestone_coverage": {"covered": [], "missed": []},
                "anti_pattern_flags": [],
                "overall_assessment": "ok",
                "limitations": [],
            }
        )
        payload = json.dumps(
            {
                "id": f"chatcmpl-{_FlakyJudgeHandler.call_count}",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _make_run(run_root: Path, case: str):
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "meta.json").write_text(
        json.dumps(
            {
                "service": "rabbitmq-experiments",
                "case": case,
                "status": "passed",
                "attempts": 1,
                "max_attempts": 3,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_root / "agent.log").write_text("[agent] thinking\n[agent] exec\n", encoding="utf-8")


def _parse_json_payload(stdout: str):
    marker = "{\n"
    pos = stdout.rfind(marker)
    if pos < 0:
        pos = stdout.rfind("{")
    if pos < 0:
        raise AssertionError(stdout)
    return json.loads(stdout[pos:])


def test_judge_post_batch_fail_open_keeps_processing_after_one_run_error():
    if importlib.util.find_spec("openai") is None:
        return

    token = uuid.uuid4().hex[:8]
    batch_root = RUNS_DIR / f"batch_2099-01-02T00-00-00Z_it_judge_partial_{token}"
    runs = [
        RUNS_DIR / f"it_judge_partial_{token}_1",
        RUNS_DIR / f"it_judge_partial_{token}_2",
        RUNS_DIR / f"it_judge_partial_{token}_3",
    ]
    for path in [batch_root, *runs]:
        shutil.rmtree(path, ignore_errors=True)
    batch_root.mkdir(parents=True, exist_ok=True)
    _make_run(runs[0], "manual_monitoring")
    _make_run(runs[1], "manual_policy_sync")
    _make_run(runs[2], "manual_skip_upgrade")
    (batch_root / "batch_index.json").write_text(
        json.dumps(
            [
                {"run_dir": str(path.relative_to(ROOT)), "service": "rabbitmq-experiments", "case": case}
                for path, case in zip(runs, ["manual_monitoring", "manual_policy_sync", "manual_skip_upgrade"])
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FlakyJudgeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])

    env_file = ROOT / ".benchmark" / f"it_judge_batch_{uuid.uuid4().hex[:8]}.env"
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
        "batch",
        "--batch-dir",
        str(batch_root),
        "--judge-env-file",
        str(env_file),
        "--judge-fail-open",
        "--judge-max-retries",
        "1",
    ]
    try:
        _FlakyJudgeHandler.call_count = 0
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
        payload = _parse_json_payload(proc.stdout)
        assert payload.get("runs_judged") == 3

        index = json.loads((batch_root / "judge_index.json").read_text(encoding="utf-8"))
        assert len(index) == 3
        statuses = [row.get("judge_status") for row in index]
        assert "error" in statuses
        assert "ok" in statuses

        summary = json.loads((batch_root / "judge_summary.json").read_text(encoding="utf-8"))
        assert summary.get("total_runs") == 3
        assert summary.get("error_runs", 0) >= 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)
        env_file.unlink(missing_ok=True)
        shutil.rmtree(batch_root, ignore_errors=True)
        for path in runs:
            shutil.rmtree(path, ignore_errors=True)
