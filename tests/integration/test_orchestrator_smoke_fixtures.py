import json
import os
import shutil
import subprocess
import threading
import time
import importlib.util
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.settings import ROOT, RUNS_DIR


def _wait_file(path, timeout=20):
    start = time.time()
    while time.time() - start < timeout:
        if path.exists():
            return
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for file: {path}")


def test_orchestrator_batch_with_fixture_service():
    fixture_service = ROOT / "tests" / "fixtures" / "resources" / "smoke-orchestrator"
    temp_service = ROOT / "resources" / "smoke-orchestrator-it"
    if temp_service.exists():
        shutil.rmtree(temp_service)
    shutil.copytree(fixture_service, temp_service)

    results_path = RUNS_DIR / "it_orchestrator_results.json"
    if results_path.exists():
        results_path.unlink()

    cmd = [
        "python3",
        "orchestrator.py",
        "batch",
        "--sandbox",
        "local",
        "--service",
        "smoke-orchestrator-it",
        "--setup-timeout",
        "1",
        "--setup-timeout-mode",
        "auto",
        "--submit-timeout",
        "20",
        "--verify-timeout",
        "20",
        "--cleanup-timeout",
        "40",
        "--max-attempts",
        "1",
        "--proxy-server",
        "127.0.0.1:65535",
        "--agent-cmd",
        "bash -c \"touch submit.signal; while [ ! -f submit_result.json ]; do sleep 0.1; done\"",
        "--results-json",
        str(results_path),
    ]
    env = dict(os.environ)
    env["BENCHMARK_PROXY_AUTOSTART"] = "0"
    env["BENCHMARK_PROXY_CONTROL_URL"] = "127.0.0.1:1"
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

    try:
        assert proc.returncode == 0, proc.stdout
        assert "setup timeout auto=" in proc.stdout
        assert results_path.exists(), "Missing orchestrator results json"
        payload = json.loads(results_path.read_text(encoding="utf-8"))
        statuses = [entry.get("result", {}).get("status") for entry in payload]
        assert statuses == ["passed", "setup_failed", "auto_failed"], statuses

        run_dirs = [entry.get("result", {}).get("run_dir") for entry in payload]
        assert all(run_dirs), run_dirs
        # Ensure final stage marker exists per run.
        for item in payload:
            rel = item.get("result", {}).get("run_dir")
            stage_file = ROOT / rel / "orchestrator_stage.json"
            _wait_file(stage_file, timeout=10)
            stage_payload = json.loads(stage_file.read_text(encoding="utf-8"))
            status = item.get("result", {}).get("status")
            expected = "setup_done" if status == "setup_failed" else "done"
            assert stage_payload.get("stage") == expected
    finally:
        if temp_service.exists():
            shutil.rmtree(temp_service)
        if results_path.exists():
            results_path.unlink()


def test_orchestrator_run_single_case_with_fixture_service():
    fixture_service = ROOT / "tests" / "fixtures" / "resources" / "smoke-orchestrator"
    temp_service = ROOT / "resources" / "smoke-orchestrator-run-it"
    if temp_service.exists():
        shutil.rmtree(temp_service)
    shutil.copytree(fixture_service, temp_service)

    cmd = [
        "python3",
        "orchestrator.py",
        "run",
        "--sandbox",
        "local",
        "--service",
        "smoke-orchestrator-run-it",
        "--case",
        "setup_auto_timeout",
        "--setup-timeout",
        "1",
        "--setup-timeout-mode",
        "auto",
        "--submit-timeout",
        "20",
        "--verify-timeout",
        "20",
        "--cleanup-timeout",
        "40",
        "--max-attempts",
        "1",
        "--proxy-server",
        "127.0.0.1:65535",
        "--agent-cmd",
        "bash -c \"touch submit.signal; while [ ! -f submit_result.json ]; do sleep 0.1; done\"",
    ]
    env = dict(os.environ)
    env["BENCHMARK_PROXY_AUTOSTART"] = "0"
    env["BENCHMARK_PROXY_CONTROL_URL"] = "127.0.0.1:1"
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

    try:
        assert proc.returncode == 0, proc.stdout
        payload_start = proc.stdout.find("[\n  {")
        assert payload_start >= 0, proc.stdout
        payload = json.loads(proc.stdout[payload_start:])
        assert len(payload) == 1
        result = payload[0].get("result") or {}
        assert result.get("status") == "passed"

        run_dir = result.get("run_dir")
        assert run_dir
        stage_file = ROOT / run_dir / "orchestrator_stage.json"
        _wait_file(stage_file, timeout=10)
        stage_payload = json.loads(stage_file.read_text(encoding="utf-8"))
        assert stage_payload.get("stage") == "done"
    finally:
        if temp_service.exists():
            shutil.rmtree(temp_service)


class _JudgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            _ = self.rfile.read(length)
        content = {
            "dimension_scores": [
                {"id": "diagnosis_speed", "score": 4, "confidence": 0.9, "evidence_ids": ["E001"], "rationale": "ok"},
                {"id": "hypothesis_quality", "score": 4, "confidence": 0.9, "evidence_ids": ["E001"], "rationale": "ok"},
                {"id": "debugging_discipline", "score": 4, "confidence": 0.9, "evidence_ids": ["E001"], "rationale": "ok"},
                {"id": "fix_robustness", "score": 4, "confidence": 0.9, "evidence_ids": ["E001"], "rationale": "ok"},
                {"id": "resource_efficiency", "score": 3, "confidence": 0.9, "evidence_ids": ["E001"], "rationale": "ok"},
            ],
            "milestone_coverage": {"covered": [], "missed": []},
            "anti_pattern_flags": [],
            "overall_assessment": "ok",
            "limitations": [],
        }
        payload = {"choices": [{"message": {"content": json.dumps(content)}}]}
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_orchestrator_batch_judge_mode_writes_artifacts():
    if importlib.util.find_spec("openai") is None:
        return

    fixture_service = ROOT / "tests" / "fixtures" / "resources" / "smoke-orchestrator"
    temp_service = ROOT / "resources" / "smoke-orchestrator-judge-it"
    if temp_service.exists():
        shutil.rmtree(temp_service)
    shutil.copytree(fixture_service, temp_service)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _JudgeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    cmd = [
        "python3",
        "orchestrator.py",
        "batch",
        "--sandbox",
        "local",
        "--service",
        "smoke-orchestrator-judge-it",
        "--setup-timeout",
        "1",
        "--setup-timeout-mode",
        "auto",
        "--submit-timeout",
        "20",
        "--verify-timeout",
        "20",
        "--cleanup-timeout",
        "40",
        "--max-attempts",
        "1",
        "--proxy-server",
        "127.0.0.1:65535",
        "--agent-cmd",
        "bash -c \"touch submit.signal; while [ ! -f submit_result.json ]; do sleep 0.1; done\"",
        "--judge-mode",
        "post-run",
        "--judge-base-url",
        f"http://127.0.0.1:{port}/v1",
        "--judge-api-key",
        "dummy-key",
        "--judge-model",
        "dummy-model",
    ]
    env = dict(os.environ)
    env["BENCHMARK_PROXY_AUTOSTART"] = "0"
    env["BENCHMARK_PROXY_CONTROL_URL"] = "127.0.0.1:1"
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

    try:
        assert proc.returncode == 0, proc.stdout
        assert "[orchestrator] judge index:" in proc.stdout

        batch_index_path = None
        for line in proc.stdout.splitlines():
            prefix = "[orchestrator] batch index: "
            if line.startswith(prefix):
                batch_index_path = Path(line[len(prefix) :].strip())
                break
        assert batch_index_path, proc.stdout
        batch_dir = batch_index_path.parent
        assert (batch_dir / "judge_index.json").exists()
        assert (batch_dir / "judge_summary.json").exists()
        assert (batch_dir / "judge_leaderboard.csv").exists()

        payload_start = proc.stdout.find("[\n  {")
        assert payload_start >= 0, proc.stdout
        payload = json.loads(proc.stdout[payload_start:])
        for item in payload:
            run = item.get("result") or {}
            run_dir = run.get("run_dir")
            assert run_dir, item
            judge_result_path = ROOT / run_dir / "judge" / "result_v1.json"
            assert judge_result_path.exists(), judge_result_path
            judge_payload = json.loads(judge_result_path.read_text(encoding="utf-8"))
            assert judge_payload.get("judge_status") == "ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
        if temp_service.exists():
            shutil.rmtree(temp_service)


# Compile CLI surface removed in Phase C (compile checks now covered by unit tests for internal modules).
