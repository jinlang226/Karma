import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app import runner as runner_mod
from app.runner import BenchmarkApp
from app.settings import ROOT, RUNS_DIR
from app.util import encode_case_id


@contextmanager
def _runner_app(resources_dir, runs_dir):
    with (
        patch.object(runner_mod, "RESOURCES_DIR", resources_dir),
        patch.object(runner_mod, "RUNS_DIR", runs_dir),
        patch.object(runner_mod, "PROXY_CONTROL_URL", None),
        patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")),
    ):
        yield BenchmarkApp()


def _wait_status(app, states, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        status = app.run_status()
        if status.get("status") in states:
            return status
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for states={states}; last={app.run_status()}")


def _wait_cleanup_done(app, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        status = app.run_status()
        if status.get("cleanup_status") == "done":
            return status
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for cleanup done; last={app.run_status()}")


def test_setup_timeout_fixture_fails_fast_and_cleans_up():
    fixture_resources = ROOT / "tests" / "fixtures" / "resources"
    run_root = RUNS_DIR / "it_runner_setup_timeout"
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    with _runner_app(fixture_resources, run_root) as app:
        case_id = encode_case_id("smoke-orchestrator", "setup_timeout_step", "test.yaml")
        started = app.start_run(case_id)
        assert started.get("status") == "started"

        status = _wait_status(app, {"setup_failed"})
        assert status.get("status") == "setup_failed"
        assert "timed out" in (status.get("last_error") or "").lower()

        status = _wait_cleanup_done(app, timeout=30)
        setup_log = ROOT / status["setup_log"]
        assert setup_log.exists()
        content = setup_log.read_text(encoding="utf-8")
        assert "Command timed out after 1s" in content

        # Reset to idle for cleanliness.
        cleaned = app.cleanup_run()
        assert cleaned.get("status") == "already_cleaned"
    shutil.rmtree(run_root, ignore_errors=True)


def test_cleanup_running_blocks_next_start():
    fixture_resources = ROOT / "tests" / "fixtures" / "resources"
    run_root = RUNS_DIR / "it_runner_cleanup_gate"
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    with _runner_app(fixture_resources, run_root) as app:
        case_fail = encode_case_id("smoke-orchestrator", "verification_fail_cleanup_gate", "test.yaml")
        case_ok = encode_case_id("smoke-orchestrator", "setup_auto_timeout", "test.yaml")

        started = app.start_run(case_fail)
        assert started.get("status") == "started"
        ready = _wait_status(app, {"ready"})
        assert ready.get("status") == "ready"

        submit = app.submit_run()
        assert submit.get("status") == "verifying"
        failed = _wait_status(app, {"auto_failed"})
        assert failed.get("status") == "auto_failed"

        blocked = app.start_run(case_ok)
        assert blocked.get("error") == "Cleanup not finished"

        _wait_cleanup_done(app, timeout=30)
        reset = app.cleanup_run()
        assert reset.get("status") == "already_cleaned"

        started_ok = app.start_run(case_ok)
        assert started_ok.get("status") == "started"
        ready_ok = _wait_status(app, {"ready"})
        assert ready_ok.get("status") == "ready"
        submit_ok = app.submit_run()
        assert submit_ok.get("status") == "verifying"
        passed_ok = _wait_status(app, {"passed"})
        assert passed_ok.get("status") == "passed"
        _wait_cleanup_done(app, timeout=30)
        app.cleanup_run()

    shutil.rmtree(run_root, ignore_errors=True)
