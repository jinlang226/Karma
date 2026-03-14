import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from app.orchestrator_core.workflow_run import (
    resolve_stage_max_attempts,
    run_workflow_stage,
    stage_setup_timeout,
    workflow_ensure_namespaces,
    workflow_machine_state_payload,
    workflow_namespace_cleanup_commands,
    workflow_namespace_ensure_plan,
    workflow_namespace_values,
    workflow_publish_prompt_and_state,
    workflow_run_final_cleanup,
    workflow_run_final_sweep,
)


class _StageApp:
    def __init__(self, *, start_error=None, run_status=None):
        self.start_error = start_error
        self._run_status = run_status or {"status": "ready"}
        self.start_calls = []

    def start_run(self, *args, **kwargs):
        self.start_calls.append({"args": args, "kwargs": kwargs})
        if self.start_error:
            return {"error": self.start_error}
        return {"status": "started"}

    def run_status(self):
        return dict(self._run_status)


def _dump_json(path, payload):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_stage_setup_timeout_auto_and_fallback_modes():
    args = SimpleNamespace(setup_timeout=30, setup_timeout_mode="auto")
    assert stage_setup_timeout({"setup_timeout_auto_sec": 45}, args) == 45
    assert stage_setup_timeout({"setup_timeout_auto_sec": "bad"}, args) == 30
    assert stage_setup_timeout({"setup_timeout_auto_sec": 10}, args) == 30

    fixed_args = SimpleNamespace(setup_timeout=30, setup_timeout_mode="fixed")
    assert stage_setup_timeout({"setup_timeout_auto_sec": 999}, fixed_args) == 30


def test_resolve_stage_max_attempts_applies_cli_cap():
    assert resolve_stage_max_attempts(None, None) is None
    assert resolve_stage_max_attempts(3, None) == 3
    assert resolve_stage_max_attempts(None, 2) == 2
    assert resolve_stage_max_attempts(3, 1) == 1
    assert resolve_stage_max_attempts(1, 3) == 1
    assert resolve_stage_max_attempts("bad", 2) == 2
    assert resolve_stage_max_attempts(2, "bad") == 2


def test_run_workflow_stage_passes_expected_start_kwargs_and_wait_timeout():
    app = _StageApp(run_status={"setup_timeout_auto_sec": 90})
    args = SimpleNamespace(setup_timeout=20, setup_timeout_mode="fixed")
    row = {
        "stage": {
            "id": "s1",
            "case_id": "case-1",
            "max_attempts": 3,
        },
        "case_data": {"name": "demo"},
        "resolved_params": {"p": 1},
        "namespace_context": {"roles": {"default": "ns-a"}, "default_role": "default"},
    }
    wait_calls = []

    def _wait_for_status(_app, wanted, timeout):
        wait_calls.append((set(wanted), int(timeout)))
        return {"status": "ready", "run_dir": "runs/s1"}

    out = run_workflow_stage(
        app,
        row,
        args,
        skip_unit_ids=["u1", "u2"],
        defer_cleanup=False,
        wait_for_status_fn=_wait_for_status,
        stage_setup_timeout_fn=lambda _status, _args: 77,
    )

    assert out["status"] == "ready"
    assert len(app.start_calls) == 1
    kwargs = app.start_calls[0]["kwargs"]
    assert kwargs["max_attempts_override"] == 3
    assert kwargs["defer_cleanup"] is False
    assert kwargs["skip_precondition_unit_ids"] == ["u1", "u2"]
    assert kwargs["case_data_override"] == {"name": "demo"}
    assert kwargs["resolved_params"] == {"p": 1}
    assert kwargs["namespace_context"]["roles"]["default"] == "ns-a"
    assert kwargs["namespace_lifecycle_owner"] == "orchestrator"
    assert wait_calls == [({"ready", "setup_failed"}, 77)]


def test_run_workflow_stage_cli_max_attempts_caps_stage_max_attempts():
    app = _StageApp(run_status={"setup_timeout_auto_sec": 90})
    args = SimpleNamespace(setup_timeout=20, setup_timeout_mode="fixed", max_attempts=1)
    row = {
        "stage": {
            "id": "s1",
            "case_id": "case-1",
            "max_attempts": 3,
        },
        "case_data": {},
        "resolved_params": {},
        "namespace_context": {},
    }

    out = run_workflow_stage(
        app,
        row,
        args,
        wait_for_status_fn=lambda *_args, **_kwargs: {"status": "ready"},
    )

    assert out["status"] == "ready"
    assert len(app.start_calls) == 1
    kwargs = app.start_calls[0]["kwargs"]
    assert kwargs["max_attempts_override"] == 1


def test_run_workflow_stage_raises_when_start_fails():
    app = _StageApp(start_error="boom")
    args = SimpleNamespace(setup_timeout=10, setup_timeout_mode="fixed")
    row = {"stage": {"case_id": "case-1"}}
    failed = False
    try:
        run_workflow_stage(
            app,
            row,
            args,
            wait_for_status_fn=lambda *_args, **_kwargs: {"status": "ready"},
        )
    except RuntimeError as exc:
        failed = True
        assert "boom" in str(exc)
    assert failed is True


def test_workflow_machine_state_payload_contains_stage_maps_and_terminal_fields():
    workflow = {
        "metadata": {"name": "wf-a"},
        "path": "workflows/wf-a.yaml",
        "spec": {"stages": [{"id": "s1"}, {"id": "s2"}]},
    }
    rows = [
        {
            "stage": {"id": "s1"},
            "resolved_params": {"x": 1},
            "param_warnings": ["warn-a"],
            "namespace_context": {"roles": {"default": "ns-1"}},
        },
        {
            "stage": {"id": "s2"},
            "resolved_params": {"y": 2},
            "param_warnings": [],
            "namespace_context": {"roles": {"default": "ns-2"}},
        },
    ]
    out = workflow_machine_state_payload(
        workflow,
        rows,
        mode="progressive",
        final_sweep_mode="full",
        active_index=1,
        stage_results=[{"status": "passed"}, None],
        solve_failed=True,
        terminal=True,
        terminal_reason="failed_at_s2",
        ts_str_fn=lambda: "2026-02-23T00-00-00Z",
    )

    assert out["workflow_name"] == "wf-a"
    assert out["final_sweep_mode"] == "full"
    assert out["stage_failure_mode"] == "continue"
    assert out["active_stage_index"] == 2
    assert out["active_stage_id"] == "s2"
    assert out["stage_total"] == 2
    assert out["stage_params"]["s1"] == {"x": 1}
    assert out["stage_param_warnings"]["s1"] == ["warn-a"]
    assert out["stage_namespaces"]["s2"] == {"default": "ns-2"}
    assert out["solve_status"] == "failed"
    assert out["terminal"] is True
    assert out["terminal_reason"] == "failed_at_s2"
    assert out["updated_at"] == "2026-02-23T00-00-00Z"


def test_workflow_publish_prompt_and_state_handles_stateful_and_non_stateful_modes():
    workflow = {
        "metadata": {"name": "wf-a"},
        "path": "workflows/wf-a.yaml",
        "spec": {"stages": [{"id": "s1"}]},
    }
    rows = [{"stage": {"id": "s1"}, "prompt_block": "hello"}]

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bundle_dir = root / "bundle"
        workflow_run_dir = root / "run"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        workflow_run_dir.mkdir(parents=True, exist_ok=True)

        workflow_publish_prompt_and_state(
            workflow=workflow,
            rows=rows,
            mode="concat_stateful",
            final_sweep_mode="full",
            active_index=0,
            stage_results=[None],
            submit_hint="submit",
            bundle_dir=bundle_dir,
            workflow_run_dir=workflow_run_dir,
            solve_failed=False,
            terminal=False,
            terminal_reason="",
            render_workflow_prompt_fn=lambda **_kwargs: "PROMPT\n",
            dump_json_fn=_dump_json,
            ts_str_fn=lambda: "2026-02-23T00-00-00Z",
        )
        assert (bundle_dir / "PROMPT.md").is_file()
        assert (workflow_run_dir / "workflow_state.json").is_file()
        assert (bundle_dir / "WORKFLOW_STATE.json").is_file()

        (bundle_dir / "WORKFLOW_STATE.json").write_text("stale", encoding="utf-8")
        workflow_publish_prompt_and_state(
            workflow=workflow,
            rows=rows,
            mode="progressive",
            final_sweep_mode="off",
            active_index=0,
            stage_results=[None],
            submit_hint="submit",
            bundle_dir=bundle_dir,
            workflow_run_dir=workflow_run_dir,
            solve_failed=False,
            terminal=False,
            terminal_reason="",
            render_workflow_prompt_fn=lambda **_kwargs: "PROMPT\n",
            dump_json_fn=_dump_json,
            ts_str_fn=lambda: "2026-02-23T00-00-00Z",
        )
        assert not (bundle_dir / "WORKFLOW_STATE.json").exists()


def test_workflow_ensure_namespaces_dedupes_and_propagates_failure_reason():
    calls = []

    def _run_cmds(cmds, log_path, default_timeout, fail_fast):
        calls.append((cmds, Path(log_path), default_timeout, fail_fast))
        return False, None, "kubectl failed"

    out = workflow_ensure_namespaces(
        ["ns-a", "ns-a", " ", "ns-b"],
        Path("runs/ns.log"),
        run_command_list_logged_fn=_run_cmds,
    )
    assert out == {"status": "failed", "error": "kubectl failed"}
    assert len(calls) == 1
    cmds = calls[0][0]
    assert len(cmds) == 2
    assert "kubectl create namespace ns-a" in cmds[0]["command"][-1]
    assert "kubectl create namespace ns-b" in cmds[1]["command"][-1]

    skipped = workflow_ensure_namespaces([], Path("runs/ns.log"), run_command_list_logged_fn=_run_cmds)
    assert skipped == {"status": "skipped", "error": None}


def test_workflow_namespace_values_combines_alias_map_and_stage_context_roles():
    rows = [
        {"namespace_context": {"roles": {"default": "ns-b", "peer": "ns-c"}}},
        {"namespace_context": {"roles": {"default": "ns-a", "other": " "}}},
        {"namespace_context": {"roles": {}}},
        {"namespace_context": None},
    ]
    out = workflow_namespace_values(rows, {"cluster_a": "ns-a", "cluster_b": "ns-b"})
    assert out == ["ns-a", "ns-b", "ns-c"]


def test_workflow_namespace_ensure_plan_skips_case_owned_roles():
    rows = [
        {
            "stage": {"id": "stage_1"},
            "namespace_context": {"roles": {"default": "ns-a", "tenant": "ns-b"}},
            "namespace_contract": {"role_ownership": {"tenant": "case"}},
        }
    ]
    alias_map = {
        "cluster_a": "ns-a",
        "cluster_b": "ns-b",
        "unused": "ns-c",
    }

    out = workflow_namespace_ensure_plan(rows, alias_map)

    assert out["values"] == ["ns-a", "ns-c"]
    assert out["skipped"] == [
        {
            "stage_id": "stage_1",
            "role": "tenant",
            "namespace": "ns-b",
            "owner": "case",
        }
    ]


def test_workflow_namespace_cleanup_commands_dedupes_values():
    out = workflow_namespace_cleanup_commands(["ns-a", "ns-a", "", "ns-b"])
    assert len(out) == 2
    assert out[0]["command"][:4] == ["kubectl", "delete", "namespace", "ns-a"]
    assert out[1]["command"][:4] == ["kubectl", "delete", "namespace", "ns-b"]


def test_workflow_run_final_cleanup_no_commands_returns_no_cleanup():
    appended = []
    out = workflow_run_final_cleanup(
        stage_contexts=[{"stage_id": "s1"}],
        workflow_run_dir=Path("runs/wf-a"),
        namespace_values=[],
        workflow_stage_cleanup_commands_fn=lambda _ctx: [],
        workflow_namespace_cleanup_commands_fn=lambda _namespaces: [],
        run_command_list_logged_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("run_command_list_logged_fn should not be called")
        ),
        append_log_line_fn=lambda _path, line: appended.append(line),
        ts_str_fn=lambda: "2026-02-23T00-00-00Z",
        relative_path_fn=lambda path: str(path),
    )
    assert out["status"] == "no_cleanup"
    assert out["cleanup_log"].endswith("workflow_cleanup.log")
    assert appended and "no cleanup commands" in appended[0]


def test_workflow_run_final_cleanup_runs_stage_then_namespace_and_tracks_failures():
    order = []

    def _stage_cmds(stage_ctx):
        stage_id = stage_ctx.get("stage_id")
        return [{"command": ["echo", stage_id], "sleep": 0}]

    def _ns_cmds(_values):
        return [{"command": ["kubectl", "delete", "namespace", "ns-a"], "sleep": 0}]

    def _run_cmds(cmds, _log, default_timeout, fail_fast, namespace_context=None):
        first = cmds[0]["command"][0]
        if first == "echo":
            stage_id = cmds[0]["command"][1]
            order.append(("stage", stage_id, namespace_context, default_timeout, fail_fast))
            if stage_id == "s1":
                return False, None, "failed stage cleanup"
            return True, None, None
        order.append(("namespace", None, namespace_context, default_timeout, fail_fast))
        return True, None, None

    out = workflow_run_final_cleanup(
        stage_contexts=[
            {"stage_id": "s1", "namespace_context": {"roles": {"default": "ns-1"}}},
            {"stage_id": "s2", "namespace_context": {"roles": {"default": "ns-2"}}},
        ],
        workflow_run_dir=Path("runs/wf-a"),
        namespace_values=["ns-a"],
        workflow_stage_cleanup_commands_fn=_stage_cmds,
        workflow_namespace_cleanup_commands_fn=_ns_cmds,
        run_command_list_logged_fn=_run_cmds,
        append_log_line_fn=lambda *_args, **_kwargs: None,
        ts_str_fn=lambda: "2026-02-23T00-00-00Z",
        relative_path_fn=lambda path: str(path),
    )
    assert out["status"] == "failed"
    assert order[0][0] == "stage"
    assert order[0][1] == "s2"
    assert order[1][0] == "stage"
    assert order[1][1] == "s1"
    assert order[2][0] == "namespace"


def test_workflow_run_final_sweep_runs_all_rows_with_namespace_context():
    calls = []

    def _oracle(case_data, log_path, namespace_context=None):
        calls.append((case_data, Path(log_path).name, namespace_context))
        return {"status": "pass"}

    rows = [
        {
            "stage": {"id": "s1"},
            "case_data": {"name": "a"},
            "namespace_context": {"roles": {"default": "ns-a"}},
        },
        {
            "stage": {"id": "s2"},
            "case_data": {"name": "b"},
            "namespace_context": {"roles": {"default": "ns-b"}},
        },
    ]
    out = workflow_run_final_sweep(rows, Path("runs/wf-a"), run_stage_oracle_stateless_fn=_oracle)
    assert out == {"s1": {"status": "pass"}, "s2": {"status": "pass"}}
    assert calls[0][1] == "workflow_final_sweep_s1.log"
    assert calls[1][1] == "workflow_final_sweep_s2.log"
    assert calls[1][2]["roles"]["default"] == "ns-b"
