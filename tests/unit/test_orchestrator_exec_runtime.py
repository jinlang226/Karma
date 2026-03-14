import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.orchestrator_core import exec_runtime


class _StepApp:
    def __init__(self, statuses, cleanup_result=None):
        self._statuses = list(statuses)
        self._idx = 0
        self._cleanup_result = cleanup_result or {"status": "no_cleanup"}
        self.cleanup_called = False

    def run_status(self):
        idx = min(self._idx, len(self._statuses) - 1)
        self._idx += 1
        return dict(self._statuses[idx])

    def cleanup_run(self):
        self.cleanup_called = True
        return dict(self._cleanup_result)


class _FakeTime:
    def __init__(self):
        self.now = 0.0

    def time(self):
        self.now += 1.0
        return self.now

    def sleep(self, seconds):
        self.now += float(seconds)


class _FakeSubprocess:
    PIPE = object()
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, result):
        self.result = result
        self.calls = []

    def run(self, cmd, **kwargs):
        self.calls.append({"cmd": cmd, **kwargs})
        return self.result


def test_wait_for_status_reaches_target_state():
    app = _StepApp([
        {"status": "setup_running"},
        {"status": "ready"},
    ])

    out = exec_runtime.wait_for_status(app, {"ready"}, timeout=5, poll=0)

    assert out["status"] == "ready"


def test_wait_for_cleanup_logs_and_returns_done():
    app = _StepApp(
        [
            {"status": "passed", "cleanup_status": "running", "cleanup_log": "runs/x/cleanup.log"},
            {"status": "passed", "cleanup_status": "done", "cleanup_log": "runs/x/cleanup.log"},
        ]
    )
    fake_time = _FakeTime()
    logs = []

    out = exec_runtime.wait_for_cleanup(
        app,
        timeout=30,
        poll=0,
        log_every=1,
        print_fn=lambda msg, flush=True: logs.append((msg, flush)),
        time_module=fake_time,
    )

    assert out["cleanup_status"] == "done"
    assert logs
    assert "waiting for cleanup" in logs[0][0]


def test_wait_for_idle_triggers_cleanup_run_for_terminal_without_cleanup_status():
    app = _StepApp([
        {"status": "passed", "cleanup_status": None, "cleanup_log": None},
    ])

    out = exec_runtime.wait_for_idle(app, poll=0)

    assert out["status"] == "passed"
    assert app.cleanup_called is True


def test_resolve_step_timeout_prefers_explicit_then_inferred_then_default():
    explicit = exec_runtime.resolve_step_timeout(
        {"timeout_sec": "45", "command": ["kubectl", "get", "pods"]},
        default_sec=300,
        parse_duration_seconds_fn=lambda raw: int(raw),
        infer_command_timeout_seconds_fn=lambda _cmd: 10,
    )
    assert explicit == 45

    inferred = exec_runtime.resolve_step_timeout(
        {"command": ["kubectl", "wait", "--timeout=30s"]},
        default_sec=300,
        parse_duration_seconds_fn=lambda _raw: None,
        infer_command_timeout_seconds_fn=lambda _cmd: 30,
    )
    assert inferred == 60

    defaulted = exec_runtime.resolve_step_timeout(
        {"command": ["kubectl", "get", "pods"]},
        default_sec=123,
        parse_duration_seconds_fn=lambda _raw: None,
        infer_command_timeout_seconds_fn=lambda _cmd: None,
    )
    assert defaulted == 123


def test_run_command_list_logged_success_writes_log():
    fake_proc = SimpleNamespace(stdout="stdout-line\n", stderr="", returncode=0)
    fake_subprocess = _FakeSubprocess(fake_proc)

    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "setup.log"
        ok, kind, reason = exec_runtime.run_command_list_logged(
            commands=[{"command": ["kubectl", "get", "pods"], "sleep": 0}],
            log_path=log_path,
            default_timeout=300,
            fail_fast=True,
            normalize_commands_fn=lambda commands: commands,
            prepare_exec_command_fn=lambda _item, _ns, render_dir=None: (["kubectl", "get", "pods"], {"X": "1"}),
            resolve_step_timeout_fn=lambda _item, default_sec=300: 12,
            command_to_string_fn=lambda cmd: " ".join(cmd),
            ts_str_fn=lambda: "2026-01-01T00:00:00Z",
            list_requires_shell_fn=lambda _cmd: False,
            safe_join_fn=lambda cmd: " ".join(cmd),
            subprocess_module=fake_subprocess,
            time_module=_FakeTime(),
            cwd=Path(tmp),
        )

        assert ok is True
        assert kind is None
        assert reason is None
        assert fake_subprocess.calls
        text = log_path.read_text(encoding="utf-8")
        assert "COMMAND 1/1" in text
        assert "stdout-line" in text
        assert "EXIT 0" in text


def test_run_command_list_logged_fail_fast_on_nonzero():
    fake_proc = SimpleNamespace(stdout="", stderr="bad", returncode=3)
    fake_subprocess = _FakeSubprocess(fake_proc)

    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "verify.log"
        ok, kind, reason = exec_runtime.run_command_list_logged(
            commands=[{"command": ["kubectl", "get", "pods"], "sleep": 0}],
            log_path=log_path,
            fail_fast=True,
            normalize_commands_fn=lambda commands: commands,
            prepare_exec_command_fn=lambda _item, _ns, render_dir=None: (["kubectl", "get", "pods"], {}),
            resolve_step_timeout_fn=lambda _item, default_sec=300: 12,
            command_to_string_fn=lambda cmd: " ".join(cmd),
            ts_str_fn=lambda: "2026-01-01T00:00:00Z",
            list_requires_shell_fn=lambda _cmd: False,
            safe_join_fn=lambda cmd: " ".join(cmd),
            subprocess_module=fake_subprocess,
            time_module=_FakeTime(),
            cwd=Path(tmp),
        )

        assert ok is False
        assert kind == "nonzero"
        assert reason == "exit=3"
