"""Unit tests for karma.runtime.manual (interactive operator mode).

The cluster-touching building blocks are replaced with fakes so the
session state machine can be exercised without kubectl.
"""

import time

import pytest

from karma.runtime import manual


class _FakeProxy:
    def __init__(self):
        self.port = 12345
        self.torn_down = False

    def teardown(self):
        self.torn_down = True


class _FakeEnv:
    def __init__(self):
        self.cleaned = False

    def bind_namespace_roles(self, roles, run_id):
        return {r: f"karma-{run_id}-{r}" for r in roles}

    def ensure_namespaces(self, role_bindings, run_dir):
        pass

    def build_namespace_env_vars(self, role_bindings):
        return {"BENCH_NAMESPACE": next(iter(role_bindings.values()), "")}

    def build_env_vars(self, role_bindings, proxy_port):
        return {"BENCH_NAMESPACE": next(iter(role_bindings.values()), ""), "PROXY": str(proxy_port)}

    def plant_decoys(self, *a, **k):
        pass

    def cleanup_namespaces(self, role_bindings, run_dir):
        self.cleaned = True


def _install_fakes(monkeypatch, *, precond_ok=True, verdict="pass"):
    env = _FakeEnv()
    proxy = _FakeProxy()
    monkeypatch.setattr(manual, "launch_proxy", lambda run_dir: proxy)
    monkeypatch.setattr(manual, "get_environment", lambda provider=None: env)
    monkeypatch.setattr(manual, "write_agent_bundle", lambda *a, **k: k.get("run_dir", "") )
    monkeypatch.setattr(
        manual, "_run_operation_units",
        lambda units, **k: {"ok": precond_ok, "units": [], "output": ""},
    )
    monkeypatch.setattr(manual, "collect_evidence", lambda **k: {"ok": True})
    monkeypatch.setattr(manual, "run_oracle", lambda *a, **k: {"verdict": verdict})
    return env, proxy


def _write_case(resources_dir, service, case):
    p = resources_dir / service / case / "test.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "prompt: do it\n"
        "namespace_contract:\n  required_roles:\n    - default\n"
        "oracle:\n  verify:\n    commands:\n      - command: 'true'\n"
    )


def _wait_until(run_id, *statuses, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manual.get_manual_status(run_id)
        if status and status["status"] in statuses:
            return status
        time.sleep(0.01)
    raise AssertionError(
        f"timed out waiting for {statuses}; last={manual.get_manual_status(run_id)}"
    )


class TestStartManualRun:
    def test_raises_for_missing_case(self, tmp_path):
        with pytest.raises(RuntimeError):
            manual.start_manual_run(
                "svc", "nope", runs_dir=tmp_path / "runs", resources_dir=tmp_path / "res"
            )

    def test_reaches_ready_after_setup(self, tmp_path, monkeypatch):
        _install_fakes(monkeypatch)
        _write_case(tmp_path / "res", "svc", "c1")
        run_id = manual.start_manual_run(
            "svc", "c1", runs_dir=tmp_path / "runs", resources_dir=tmp_path / "res"
        )
        status = _wait_until(run_id, "ready")
        assert status["phase"] == "ready"
        assert status["namespace_bindings"]["default"].endswith("-default")
        assert "kubeconfig_path" in status
        manual.cleanup_manual_run(run_id)

    def test_precondition_failure_sets_setup_failed(self, tmp_path, monkeypatch):
        env, proxy = _install_fakes(monkeypatch, precond_ok=False)
        _write_case(tmp_path / "res", "svc", "c1")
        run_id = manual.start_manual_run(
            "svc", "c1", runs_dir=tmp_path / "runs", resources_dir=tmp_path / "res"
        )
        status = _wait_until(run_id, "setup_failed")
        assert "precondition" in (status.get("error") or "")
        # namespaces and proxy must be torn down on setup failure, not leaked
        assert env.cleaned is True
        assert proxy.torn_down is True
        manual.cleanup_manual_run(run_id)

    def test_status_excludes_internal_objects(self, tmp_path, monkeypatch):
        _install_fakes(monkeypatch)
        _write_case(tmp_path / "res", "svc", "c1")
        run_id = manual.start_manual_run(
            "svc", "c1", runs_dir=tmp_path / "runs", resources_dir=tmp_path / "res"
        )
        _wait_until(run_id, "ready")
        status = manual.get_manual_status(run_id)
        assert not any(k.startswith("_") for k in status)
        manual.cleanup_manual_run(run_id)


class TestSubmitManualRun:
    def test_pass_verdict_marks_passed(self, tmp_path, monkeypatch):
        _install_fakes(monkeypatch, verdict="pass")
        _write_case(tmp_path / "res", "svc", "c1")
        run_id = manual.start_manual_run(
            "svc", "c1", runs_dir=tmp_path / "runs", resources_dir=tmp_path / "res"
        )
        _wait_until(run_id, "ready")
        result = manual.submit_manual_run(run_id)
        assert result["status"] == "passed"
        assert result["attempts"] == 1
        manual.cleanup_manual_run(run_id)

    def test_fail_verdict_marks_failed_and_is_retryable(self, tmp_path, monkeypatch):
        _install_fakes(monkeypatch, verdict="fail")
        _write_case(tmp_path / "res", "svc", "c1")
        run_id = manual.start_manual_run(
            "svc", "c1", runs_dir=tmp_path / "runs", resources_dir=tmp_path / "res"
        )
        _wait_until(run_id, "ready")
        first = manual.submit_manual_run(run_id)
        assert first["status"] == "failed"
        # failed runs can be submitted again
        second = manual.submit_manual_run(run_id)
        assert second["attempts"] == 2
        manual.cleanup_manual_run(run_id)

    def test_submit_unknown_raises(self):
        with pytest.raises(RuntimeError, match="unknown"):
            manual.submit_manual_run("no-such-run")

    def test_submit_before_ready_raises(self, tmp_path, monkeypatch):
        from karma.runtime import manual as m
        m._register("frozen", {"run_id": "frozen", "status": "setup_running"})
        with pytest.raises(RuntimeError, match="not ready"):
            m.submit_manual_run("frozen")
        m._sessions.pop("frozen", None)


class TestCleanupManualRun:
    def test_tears_down_and_forgets(self, tmp_path, monkeypatch):
        env, proxy = _install_fakes(monkeypatch)
        _write_case(tmp_path / "res", "svc", "c1")
        run_id = manual.start_manual_run(
            "svc", "c1", runs_dir=tmp_path / "runs", resources_dir=tmp_path / "res"
        )
        _wait_until(run_id, "ready")
        result = manual.cleanup_manual_run(run_id)
        assert result["status"] == "cleaned"
        assert proxy.torn_down is True
        assert env.cleaned is True
        assert manual.get_manual_status(run_id) is None

    def test_cleanup_unknown_is_safe(self):
        assert manual.cleanup_manual_run("nope")["status"] == "unknown"

    def test_cleanup_deletes_recorded_literal_namespaces_without_roles(self, tmp_path):
        # SW-1: a required_roles:[] case (role_bindings={}) that created a literal
        # namespace (mongodb/cockroachdb/spark) must still get it deleted, via
        # cleanup_created_namespaces -- the per-role cleanup covers nothing here.
        calls = {}

        class _Env:
            def cleanup_namespaces(self, rb, run_dir):
                calls["role"] = dict(rb)

            def cleanup_created_namespaces(self, created, run_dir):
                calls["created"] = set(created)

        with manual._lock:
            manual._sessions["mrun"] = {
                "run_id": "mrun", "service": "mongodb", "case_name": "deploy",
                "_env": _Env(), "_role_bindings": {}, "_ns_created": {"mongodb"},
                "_stage_dir": tmp_path, "_proxy": None,
            }
        try:
            result = manual.cleanup_manual_run("mrun")
            assert result["status"] == "cleaned"
            assert calls.get("created") == {"mongodb"}   # literal ns deleted
            assert "role" not in calls                    # no role ns to clean
        finally:
            manual._sessions.pop("mrun", None)
