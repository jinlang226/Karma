import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.orchestrator_core.workflow_engine import run_workflow


class _DummyApp:
    def __init__(self, *, run_status=None, submit_state=None):
        self._run_status = run_status or {"run_dir": "runs/stage_1"}
        self._submit_state = submit_state or {"status": "verifying"}

    def run_status(self):
        return dict(self._run_status)

    def submit_run(self):
        return dict(self._submit_state)


def _write_json(path, payload):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _append_jsonl(path, payload):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _base_context(root: Path, *, app=None, args_overrides=None, dep_overrides=None):
    root.mkdir(parents=True, exist_ok=True)
    app = app or _DummyApp()
    args = SimpleNamespace(
        workflow="workflows/demo.yaml",
        submit_timeout=30,
        verify_timeout=30,
        manual_start=False,
        sandbox="local",
        real_kubectl="",
        agent_auth_path="",
        agent_auth_dest="",
    )
    for key, value in (args_overrides or {}).items():
        setattr(args, key, value)

    workflow = {
        "metadata": {"name": "wf-unit"},
        "path": "workflows/demo.yaml",
        "spec": {
            "prompt_mode": "progressive",
            "stages": [{"id": "stage_1", "service": "svc", "case": "case"}],
        },
    }
    rows = [
        {
            "stage": {"id": "stage_1", "service": "svc", "case": "case", "case_id": "cid-1"},
            "case_data": {},
            "resolved_params": {},
            "param_warnings": [],
            "namespace_context": {"default_role": "default", "roles": {"default": "cmp-wf-default"}},
        }
    ]

    def _prepare_bundle(_app, _case_id, workflow_run_dir, _args, **_kwargs):
        bundle_dir = Path(workflow_run_dir) / "agent_bundle"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        submit_file = bundle_dir / "submit.signal"
        submit_result_file = bundle_dir / "submit_result.json"
        start_file = bundle_dir / "start.signal"
        return bundle_dir, submit_file, submit_result_file, start_file, None, "kubectl"

    deps = {
        "root": root,
        "resources_dir": root / "resources",
        "load_workflow_spec_fn": lambda _path: workflow,
        "resolve_workflow_rows_fn": lambda _app, _workflow: rows,
        "wait_for_idle_fn": lambda *_args, **_kwargs: None,
        "ts_str_fn": lambda *_args, **_kwargs: "2026-02-23T00-00-00Z",
        "attach_workflow_namespace_context_fn": lambda _rows, _workflow, **_kwargs: {"default": "cmp-wf-default"},
        "workflow_effective_params_payload_fn": lambda _rows: {"stage_1": {"params": {}}},
        "dump_json_fn": _write_json,
        "workflow_ensure_namespaces_fn": lambda _values, _log_path: {"status": "ok", "error": None},
        "workflow_run_final_cleanup_fn": lambda *_args, **_kwargs: {
            "status": "done",
            "cleanup_log": "runs/wf/workflow_cleanup.log",
        },
        "run_workflow_stage_fn": lambda *_args, **_kwargs: {
            "status": "ready",
            "run_dir": "runs/stage_1",
            "last_error": None,
        },
        "workflow_transition_log_fn": lambda *_args, **_kwargs: None,
        "prepare_bundle_fn": _prepare_bundle,
        "workflow_publish_prompt_and_state_fn": lambda *_args, **_kwargs: None,
        "prepare_agent_auth_mount_fn": lambda *_args, **_kwargs: (None, None),
        "namespace_env_vars_fn": lambda *_args, **_kwargs: {},
        "launch_agent_fn": lambda *_args, **_kwargs: object(),
        "write_stage_fn": lambda *_args, **_kwargs: None,
        "wait_for_start_signal_fn": lambda *_args, **_kwargs: None,
        "stream_action_trace_fn": lambda *_args, **_kwargs: None,
        "stream_agent_log_fn": lambda *_args, **_kwargs: None,
        "wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: (None, None),
        "write_submit_result_fn": _write_json,
        "append_submit_result_log_fn": lambda *_args, **_kwargs: None,
        "workflow_submit_payload_fn": lambda **kwargs: {
            "status": kwargs.get("base_status"),
            "attempt": kwargs.get("attempt"),
            "can_retry": bool(kwargs.get("can_retry")),
            "workflow": {
                "stage_id": kwargs.get("stage_id"),
                "reason": kwargs.get("reason"),
                "final": bool(kwargs.get("final_flag")),
            },
        },
        "wait_for_status_fn": lambda *_args, **_kwargs: {
            "status": "passed",
            "attempts": 1,
            "max_attempts": 1,
            "elapsed_seconds": 1,
            "time_limit_seconds": 10,
            "verification_logs": ["runs/stage_1/verification_1.log"],
            "run_dir": "runs/stage_1",
            "last_error": None,
        },
        "workflow_status_from_stage_fn": lambda _final: ("passed", "stage_passed"),
        "workflow_append_stage_result_fn": _append_jsonl,
        "workflow_run_final_sweep_fn": lambda *_args, **_kwargs: {"stage_1": {"status": "pass"}},
        "run_stage_oracle_stateless_fn": lambda *_args, **_kwargs: {"status": "pass"},
        "render_workflow_prompt_fn": lambda *_args, **_kwargs: "",
        "attach_agent_usage_fields_fn": lambda payload: payload,
        "terminate_agent_fn": lambda *_args, **_kwargs: None,
    }
    deps.update(dep_overrides or {})
    return app, args, deps


def test_namespace_setup_failure_returns_setup_failed():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        app, args, deps = _base_context(
            root,
            dep_overrides={
                "workflow_ensure_namespaces_fn": lambda *_args, **_kwargs: {
                    "status": "failed",
                    "error": "namespace bootstrap failed",
                }
            },
        )
        out = run_workflow(app, args, **deps)
    assert out["status"] == "setup_failed"
    assert out["reason"] == "namespace_setup_failed"
    assert out["last_error"] == "namespace bootstrap failed"
    assert out["cleanup_status"] == "done"
    assert out["terminal_base_status"] == "setup_failed"


def test_namespace_setup_and_cleanup_use_row_namespace_context_when_alias_map_empty():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        captured = {"ensure": None, "cleanup": []}

        def _ensure(values, _log_path):
            captured["ensure"] = list(values or [])
            return {"status": "ok", "error": None}

        def _cleanup(*_args, **kwargs):
            captured["cleanup"].append(list(kwargs.get("namespace_values") or []))
            return {"status": "done", "cleanup_log": "runs/wf/workflow_cleanup.log"}

        app, args, deps = _base_context(
            root,
            dep_overrides={
                "attach_workflow_namespace_context_fn": lambda _rows, _workflow, **_kwargs: {},
                "workflow_ensure_namespaces_fn": _ensure,
                "workflow_run_final_cleanup_fn": _cleanup,
                "wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: (None, None),
            },
        )
        out = run_workflow(app, args, **deps)

    assert out["status"] == "workflow_fatal"
    assert out["terminal_reason"] == "submit_timeout"
    assert captured["ensure"] == ["cmp-wf-default"]
    assert captured["cleanup"]
    assert captured["cleanup"][-1] == ["cmp-wf-default"]


def test_namespace_ensure_plan_filters_precreation_but_cleanup_uses_all_namespaces():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        captured = {"ensure": None, "cleanup": []}

        rows = [
            {
                "stage": {"id": "stage_1", "service": "svc", "case": "case", "case_id": "cid-1"},
                "case_data": {},
                "resolved_params": {},
                "param_warnings": [],
                "namespace_contract": {
                    "required_roles": ["default", "tenant"],
                    "default_role": "default",
                    "role_ownership": {"tenant": "case"},
                },
                "namespace_context": {
                    "default_role": "default",
                    "roles": {"default": "cmp-wf-default", "tenant": "cmp-wf-tenant"},
                },
            }
        ]

        def _ensure(values, _log_path):
            captured["ensure"] = list(values or [])
            return {"status": "ok", "error": None}

        def _cleanup(*_args, **kwargs):
            captured["cleanup"].append(list(kwargs.get("namespace_values") or []))
            return {"status": "done", "cleanup_log": "runs/wf/workflow_cleanup.log"}

        app, args, deps = _base_context(
            root,
            dep_overrides={
                "resolve_workflow_rows_fn": lambda _app, _workflow: rows,
                "attach_workflow_namespace_context_fn": lambda _rows, _workflow, **_kwargs: {},
                "workflow_namespace_ensure_plan_fn": lambda _rows, _alias: {
                    "values": ["cmp-wf-default"],
                    "skipped": [
                        {
                            "stage_id": "stage_1",
                            "role": "tenant",
                            "namespace": "cmp-wf-tenant",
                            "owner": "case",
                        }
                    ],
                },
                "workflow_ensure_namespaces_fn": _ensure,
                "workflow_run_final_cleanup_fn": _cleanup,
                "wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: (None, None),
            },
        )
        out = run_workflow(app, args, **deps)

    assert out["status"] == "workflow_fatal"
    assert out["terminal_reason"] == "submit_timeout"
    assert captured["ensure"] == ["cmp-wf-default"]
    assert captured["cleanup"]
    assert captured["cleanup"][-1] == ["cmp-wf-default", "cmp-wf-tenant"]


def test_first_stage_setup_failure_returns_setup_failed():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        app, args, deps = _base_context(
            root,
            dep_overrides={
                "run_workflow_stage_fn": lambda *_args, **_kwargs: {
                    "status": "setup_failed",
                    "run_dir": "runs/stage_1",
                    "last_error": "setup failed",
                }
            },
        )
        out = run_workflow(app, args, **deps)
    assert out["status"] == "setup_failed"
    assert out["reason"] == "stage_setup_failed"
    assert out["stage"] == "stage_1"
    assert out["last_error"] == "setup failed"


def test_manual_start_agent_exit_returns_agent_failed():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        app, args, deps = _base_context(
            root,
            args_overrides={"manual_start": True},
            dep_overrides={"wait_for_start_signal_fn": lambda *_args, **_kwargs: 19},
        )
        out = run_workflow(app, args, **deps)
    assert out["status"] == "agent_failed"
    assert out["agent_exit_code"] == 19
    assert out["terminal_base_status"] == "agent_failed"
    assert out["cleanup_status"] == "done"


def test_submit_timeout_returns_workflow_fatal_terminal_reason():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        app, args, deps = _base_context(
            root,
            dep_overrides={"wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: (None, None)},
        )
        out = run_workflow(app, args, **deps)
    assert out["status"] == "workflow_fatal"
    assert out["terminal_reason"] == "submit_timeout"
    assert out["terminal_base_status"] == "submit_timeout"


def test_interrupt_during_wait_still_runs_cleanup_and_skips_final_sweep():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        calls = {"cleanup": 0, "sweep": 0}

        def _cleanup(*_args, **_kwargs):
            calls["cleanup"] += 1
            return {"status": "done", "cleanup_log": "runs/wf/workflow_cleanup.log"}

        def _sweep(*_args, **_kwargs):
            calls["sweep"] += 1
            return {"stage_1": {"status": "pass"}}

        app, args, deps = _base_context(
            root,
            dep_overrides={
                "wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
                "workflow_run_final_cleanup_fn": _cleanup,
                "workflow_run_final_sweep_fn": _sweep,
            },
        )
        out = run_workflow(app, args, **deps)
        sweep_payload = json.loads((root / out["workflow_final_sweep_path"]).read_text(encoding="utf-8"))

    assert out["status"] == "workflow_fatal"
    assert out["terminal_reason"] == "interrupted"
    assert out["terminal_base_status"] == "interrupted"
    assert out["cleanup_status"] == "done"
    assert calls["cleanup"] == 1
    assert calls["sweep"] == 0
    assert sweep_payload["status"] == "skipped"
    assert sweep_payload["reason"] == "terminated_early"


def test_workflow_final_sweep_is_observed_only():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        app, args, deps = _base_context(root)
        out = run_workflow(app, args, **deps)

        sweep_path = root / out["workflow_final_sweep_path"]
        payload = json.loads(sweep_path.read_text(encoding="utf-8"))

    assert payload["expected_final"] == {}
    assert payload["regression"] == {}
    assert payload["observed_final"] == {"stage_1": "pass"}
    assert payload["regression_analysis"]["status"] == "not_available"
    assert isinstance(payload["regression_analysis"].get("reason"), str)


def test_workflow_final_sweep_can_be_disabled():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        workflow = {
            "metadata": {"name": "wf-unit"},
            "path": "workflows/demo.yaml",
            "spec": {
                "prompt_mode": "progressive",
                "final_sweep_mode": "off",
                "stages": [{"id": "stage_1", "service": "svc", "case": "case"}],
            },
        }
        calls = {"count": 0}

        def _sweep(*_args, **_kwargs):
            calls["count"] += 1
            return {"stage_1": {"status": "pass"}}

        app, args, deps = _base_context(
            root,
            dep_overrides={
                "load_workflow_spec_fn": lambda _path: workflow,
                "wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: ({}, None),
                "workflow_run_final_sweep_fn": _sweep,
            },
        )
        out = run_workflow(app, args, **deps)
        sweep_path = root / out["workflow_final_sweep_path"]
        payload = json.loads(sweep_path.read_text(encoding="utf-8"))

    assert calls["count"] == 0
    assert out["workflow_final_sweep_mode"] == "off"
    assert payload["status"] == "skipped"
    assert payload["mode"] == "off"
    assert payload["reason"] == "disabled_by_config"
    assert payload["details"] == {}
    assert payload["observed_final"] == {}


def test_workflow_final_sweep_cli_override_disables_even_when_workflow_is_full():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        calls = {"count": 0}

        def _sweep(*_args, **_kwargs):
            calls["count"] += 1
            return {"stage_1": {"status": "pass"}}

        app, args, deps = _base_context(
            root,
            args_overrides={"final_sweep_mode": "off"},
            dep_overrides={
                "wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: ({}, None),
                "workflow_run_final_sweep_fn": _sweep,
            },
        )
        out = run_workflow(app, args, **deps)
        sweep_path = root / out["workflow_final_sweep_path"]
        payload = json.loads(sweep_path.read_text(encoding="utf-8"))

    assert calls["count"] == 0
    assert payload["mode"] == "off"
    assert payload["status"] == "skipped"


def test_agent_exit_returns_workflow_fatal_and_exit_code():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        app, args, deps = _base_context(
            root,
            dep_overrides={"wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: (None, 23)},
        )
        out = run_workflow(app, args, **deps)
    assert out["status"] == "workflow_fatal"
    assert out["terminal_reason"] == "agent_exited"
    assert out["terminal_base_status"] == "auto_failed"
    assert out["agent_exit_code"] == 23


def test_submit_error_branch_returns_workflow_fatal():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        app = _DummyApp(submit_state={"error": "submit failed"})
        app, args, deps = _base_context(
            root,
            app=app,
            dep_overrides={"wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: ({}, None)},
        )
        out = run_workflow(app, args, **deps)
    assert out["status"] == "workflow_fatal"
    assert out["terminal_reason"] == "submit_error:submit failed"
    assert out["terminal_base_status"] == "auto_failed"


def test_cleanup_control_payload_returns_workflow_fatal_manual_cleanup():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        app, args, deps = _base_context(
            root,
            dep_overrides={"wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: ('{"action":"cleanup"}', None)},
        )
        out = run_workflow(app, args, **deps)
    assert out["status"] == "workflow_fatal"
    assert out["terminal_reason"] == "manual_cleanup"
    assert out["terminal_base_status"] == "auto_failed"
    assert out["cleanup_status"] == "done"


def test_submit_receipt_ack_written_before_submit_result():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        app, args, deps = _base_context(
            root,
            dep_overrides={"wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: ({}, None)},
        )
        out = run_workflow(app, args, **deps)

        run_dir = root / out["run_dir"]
        ack_path = run_dir / "agent_bundle" / "submit.ack"
        submit_result_path = run_dir / "agent_bundle" / "submit_result.json"
        assert out["status"] == "passed"
        assert ack_path.exists()
        ack_payload = json.loads(ack_path.read_text(encoding="utf-8"))
        assert ack_payload.get("status") == "received"
        assert isinstance(ack_payload.get("ts"), str) and ack_payload.get("ts")
        assert ack_payload.get("stage_id") == "stage_1"

        submit_payload = json.loads(submit_result_path.read_text(encoding="utf-8"))
        wf_payload = submit_payload.get("workflow") or {}
        assert wf_payload.get("stage_id") == "stage_1"
        assert wf_payload.get("final") is True


def test_verify_timeout_branch_returns_workflow_fatal():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        app, args, deps = _base_context(
            root,
            dep_overrides={
                "wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: ({}, None),
                "wait_for_status_fn": lambda *_args, **_kwargs: None,
            },
        )
        out = run_workflow(app, args, **deps)
    assert out["status"] == "workflow_fatal"
    assert out["terminal_reason"] == "verify_timeout"
    assert out["terminal_base_status"] == "verify_timeout"


def test_stage_failure_mode_terminate_stops_before_next_stage_setup():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        workflow = {
            "metadata": {"name": "wf-unit"},
            "path": "workflows/demo.yaml",
            "spec": {
                "prompt_mode": "progressive",
                "stage_failure_mode": "terminate",
                "final_sweep_mode": "off",
                "stages": [
                    {"id": "stage_1", "service": "svc", "case": "case", "case_id": "cid-1"},
                    {"id": "stage_2", "service": "svc", "case": "case", "case_id": "cid-2"},
                ],
            },
        }
        rows = [
            {
                "stage": {"id": "stage_1", "service": "svc", "case": "case", "case_id": "cid-1"},
                "case_data": {},
                "resolved_params": {},
                "param_warnings": [],
                "namespace_context": {"default_role": "default", "roles": {"default": "cmp-wf-default"}},
            },
            {
                "stage": {"id": "stage_2", "service": "svc", "case": "case", "case_id": "cid-2"},
                "case_data": {},
                "resolved_params": {},
                "param_warnings": [],
                "namespace_context": {"default_role": "default", "roles": {"default": "cmp-wf-default"}},
            },
        ]
        setup_calls = []

        def _run_stage(_app, row, _args, **_kwargs):
            setup_calls.append((row.get("stage") or {}).get("id"))
            return {"status": "ready", "run_dir": f"runs/{setup_calls[-1]}", "last_error": None}

        app, args, deps = _base_context(
            root,
            dep_overrides={
                "load_workflow_spec_fn": lambda _path: workflow,
                "resolve_workflow_rows_fn": lambda _app, _workflow: rows,
                "run_workflow_stage_fn": _run_stage,
                "wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: ({}, None),
                "wait_for_status_fn": lambda *_args, **_kwargs: {
                    "status": "failed",
                    "attempts": 1,
                    "max_attempts": 1,
                    "elapsed_seconds": 1,
                    "time_limit_seconds": 10,
                    "verification_logs": ["runs/stage_1/verification_1.log"],
                    "run_dir": "runs/stage_1",
                    "last_error": "oracle failed",
                    "last_verification_kind": "oracle_failed",
                },
                "workflow_status_from_stage_fn": lambda _final: ("failed", "oracle_failed"),
            },
        )
        out = run_workflow(app, args, **deps)
        run_dir = root / out["run_dir"]
        submit_result = json.loads((run_dir / "agent_bundle" / "submit_result.json").read_text(encoding="utf-8"))

    assert setup_calls == ["stage_1"]
    assert out["status"] == "failed"
    assert out["terminal_reason"] == "stage_failed_terminate"
    assert out["workflow_stage_failure_mode"] == "terminate"
    assert submit_result.get("workflow", {}).get("final") is True
    assert submit_result.get("workflow", {}).get("reason") == "stage_failed_nonretryable_terminate"


def test_cli_stage_failure_mode_override_continue_allows_advance():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        workflow = {
            "metadata": {"name": "wf-unit"},
            "path": "workflows/demo.yaml",
            "spec": {
                "prompt_mode": "progressive",
                "stage_failure_mode": "terminate",
                "final_sweep_mode": "off",
                "stages": [
                    {"id": "stage_1", "service": "svc", "case": "case", "case_id": "cid-1"},
                    {"id": "stage_2", "service": "svc", "case": "case", "case_id": "cid-2"},
                ],
            },
        }
        rows = [
            {
                "stage": {"id": "stage_1", "service": "svc", "case": "case", "case_id": "cid-1"},
                "case_data": {},
                "resolved_params": {},
                "param_warnings": [],
                "namespace_context": {"default_role": "default", "roles": {"default": "cmp-wf-default"}},
            },
            {
                "stage": {"id": "stage_2", "service": "svc", "case": "case", "case_id": "cid-2"},
                "case_data": {},
                "resolved_params": {},
                "param_warnings": [],
                "namespace_context": {"default_role": "default", "roles": {"default": "cmp-wf-default"}},
            },
        ]
        setup_calls = []
        submit_calls = {"count": 0}

        def _run_stage(_app, row, _args, **_kwargs):
            setup_calls.append((row.get("stage") or {}).get("id"))
            return {"status": "ready", "run_dir": f"runs/{setup_calls[-1]}", "last_error": None}

        def _wait_submit(*_args, **_kwargs):
            submit_calls["count"] += 1
            return ({}, None)

        def _wait_status(*_args, **_kwargs):
            idx = submit_calls["count"]
            if idx == 1:
                return {
                    "status": "failed",
                    "attempts": 1,
                    "max_attempts": 1,
                    "elapsed_seconds": 1,
                    "time_limit_seconds": 10,
                    "verification_logs": ["runs/stage_1/verification_1.log"],
                    "run_dir": "runs/stage_1",
                    "last_error": "oracle failed",
                    "last_verification_kind": "oracle_failed",
                }
            return {
                "status": "passed",
                "attempts": 1,
                "max_attempts": 1,
                "elapsed_seconds": 1,
                "time_limit_seconds": 10,
                "verification_logs": ["runs/stage_2/verification_1.log"],
                "run_dir": "runs/stage_2",
                "last_error": None,
                "last_verification_kind": "oracle_passed",
            }

        def _stage_status(final):
            if final.get("status") == "failed":
                return "failed", "oracle_failed"
            return "passed", "stage_passed"

        app, args, deps = _base_context(
            root,
            args_overrides={"stage_failure_mode": "continue"},
            dep_overrides={
                "load_workflow_spec_fn": lambda _path: workflow,
                "resolve_workflow_rows_fn": lambda _app, _workflow: rows,
                "run_workflow_stage_fn": _run_stage,
                "wait_for_submit_or_agent_fn": _wait_submit,
                "wait_for_status_fn": _wait_status,
                "workflow_status_from_stage_fn": _stage_status,
            },
        )
        out = run_workflow(app, args, **deps)

    assert setup_calls == ["stage_1", "stage_2"]
    assert out["status"] == "failed"
    assert out["terminal_reason"] == "workflow_complete"
    assert out["workflow_stage_failure_mode"] == "continue"


def test_concat_blind_submit_result_redacts_stage_identifiers():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        workflow = {
            "metadata": {"name": "wf-unit"},
            "path": "workflows/demo.yaml",
            "spec": {
                "prompt_mode": "concat_blind",
                "stages": [{"id": "stage_1", "service": "svc", "case": "case"}],
            },
        }
        app, args, deps = _base_context(
            root,
            dep_overrides={
                "load_workflow_spec_fn": lambda _path: workflow,
                "wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: ({}, None),
            },
        )
        out = run_workflow(app, args, **deps)

        run_dir = root / out["run_dir"]
        submit_result_path = run_dir / "agent_bundle" / "submit_result.json"
        submit_payload = json.loads(submit_result_path.read_text(encoding="utf-8"))

    assert "verification_log" not in submit_payload
    wf_payload = submit_payload.get("workflow") or {}
    assert wf_payload.get("final") is True
    for key in (
        "stage_index",
        "stage_total",
        "stage_id",
        "stage_attempt",
        "stage_status",
        "next_stage_id",
    ):
        assert key not in wf_payload


def test_workflow_stage_run_dirs_are_nested_under_workflow_run_directory():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        workflow = {
            "metadata": {"name": "wf-unit"},
            "path": "workflows/demo.yaml",
            "spec": {
                "prompt_mode": "progressive",
                "stages": [
                    {"id": "stage_1", "service": "svc", "case": "case", "case_id": "cid-1"},
                    {"id": "stage_2", "service": "svc", "case": "case", "case_id": "cid-2"},
                ],
            },
        }
        rows = [
            {
                "stage": {"id": "stage_1", "service": "svc", "case": "case", "case_id": "cid-1"},
                "case_data": {},
                "resolved_params": {},
                "param_warnings": [],
                "namespace_context": {"default_role": "default", "roles": {"default": "cmp-wf-default"}},
            },
            {
                "stage": {"id": "stage_2", "service": "svc", "case": "case", "case_id": "cid-2"},
                "case_data": {},
                "resolved_params": {},
                "param_warnings": [],
                "namespace_context": {"default_role": "default", "roles": {"default": "cmp-wf-default"}},
            },
        ]
        seen_stage_dirs = []

        def _run_stage(_app, _row, _args, **kwargs):
            stage_dir = Path(kwargs.get("stage_run_dir"))
            seen_stage_dirs.append(stage_dir)
            return {"status": "ready", "run_dir": str(stage_dir.relative_to(root))}

        app, args, deps = _base_context(
            root,
            dep_overrides={
                "load_workflow_spec_fn": lambda _path: workflow,
                "resolve_workflow_rows_fn": lambda _app, _workflow: rows,
                "run_workflow_stage_fn": _run_stage,
                "wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: ({}, None),
            },
        )
        out = run_workflow(app, args, **deps)

        run_dir = root / out["run_dir"]
        assert len(seen_stage_dirs) == 2
        assert seen_stage_dirs[0] == run_dir / "stage_runs" / "01_stage_1"
        assert seen_stage_dirs[1] == run_dir / "stage_runs" / "02_stage_2"


def test_workflow_writes_final_sweep_and_cleanup_stages():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td).resolve()
        stage_events = []

        def _write_stage(_run_dir, stage, detail=None):
            stage_events.append((str(stage), str(detail or "")))

        app, args, deps = _base_context(
            root,
            dep_overrides={
                "wait_for_submit_or_agent_fn": lambda *_args, **_kwargs: ({}, None),
                "write_stage_fn": _write_stage,
            },
        )
        out = run_workflow(app, args, **deps)

    assert out["status"] == "passed"
    stage_names = [name for name, _detail in stage_events]
    assert "final_sweep" in stage_names
    assert "workflow_cleanup" in stage_names
    assert stage_names[-1] == "done"
