import tempfile
from pathlib import Path

from app.orchestrator_core.workflow_run import (
    workflow_machine_state_payload,
    workflow_submit_payload,
)
from app.orchestrator_core.workflow_validation import workflow_namespace_hygiene_violations
from app.workflow import (
    build_alias_namespace_map,
    load_workflow_spec,
    render_case_prompt_block,
    render_workflow_prompt,
    resolve_stage_namespace_context,
)


def test_load_workflow_spec_case_ref_and_case_id():
    with tempfile.TemporaryDirectory() as tmp:
        wf_path = Path(tmp) / "wf.yaml"
        wf_path.write_text(
            """
apiVersion: benchmark/v1
kind: Workflow
metadata:
  name: unit-workflow
spec:
  prompt_mode: progressive
  stages:
    - id: stage_a
      case_ref:
        service: workflow-mock
        case: stage_seed
    - id: stage_b
      case_ref:
        service: workflow-mock
        case: stage_scale
""".strip()
            + "\n",
            encoding="utf-8",
        )

        spec = load_workflow_spec(str(wf_path))
        stages = spec["spec"]["stages"]
        assert [s["id"] for s in stages] == ["stage_a", "stage_b"]
        assert spec["spec"]["final_sweep_mode"] == "full"
        assert spec["spec"]["stage_failure_mode"] == "continue"
        assert stages[0]["case_ref"]["service"] == "workflow-mock"
        assert stages[0]["case_ref"]["case"] == "stage_seed"
        assert isinstance(stages[0]["case_id"], str) and stages[0]["case_id"]
        assert stages[0]["param_overrides"] == {}


def test_load_workflow_spec_resolves_stage_case_path():
    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(dir=repo_root) as tmp:
        tmp_path = Path(tmp)
        case_dir = tmp_path / "cases" / "stage_seed"
        case_dir.mkdir(parents=True, exist_ok=True)
        case_file = case_dir / "test.yaml"
        case_file.write_text("type: mock\n", encoding="utf-8")

        wf_path = tmp_path / "wf.yaml"
        wf_path.write_text(
            """
apiVersion: benchmark/v1
kind: Workflow
metadata:
  name: unit-workflow
spec:
  prompt_mode: progressive
  stages:
    - id: stage_a
      case_ref:
        service: workflow-mock
        case: stage_seed
      case_path: ./cases/stage_seed/test.yaml
""".strip()
            + "\n",
            encoding="utf-8",
        )

        spec = load_workflow_spec(str(wf_path))
        stage = (spec.get("spec") or {}).get("stages")[0]
        assert stage.get("case_path") == str(case_file.resolve())


def test_render_workflow_prompt_progressive_and_concat_modes():
    workflow = {
        "metadata": {"name": "wf"},
        "spec": {
            "stages": [
                {"id": "s1"},
                {"id": "s2"},
            ]
        },
    }
    blocks = ["# stage1\nDo A", "# stage2\nDo B"]

    progressive = render_workflow_prompt(
        workflow=workflow,
        mode="progressive",
        active_index=0,
        case_blocks=blocks,
        stage_results=[None, None],
    )
    assert "Active Stage: 1/2 (s1)" in progressive
    assert "# stage1" in progressive
    assert "# stage2" not in progressive
    assert "Execution Protocol" in progressive
    assert "Workflow Summary" in progressive
    assert "Prompt Mode" in progressive
    assert "Feedback Files" in progressive
    assert "Post-Run Validation" in progressive
    assert "`submit.ack`" in progressive
    assert "`submit_result.json`" in progressive
    assert "After final submission, the system runs a full verification sweep across all workflow stages against the final cluster state." in progressive
    assert "Some drift can be acceptable, but less drift is better." in progressive
    assert "Only read `submit_result.json` after `submit.ack`" in progressive
    assert "Before `submit.ack`, `submit_result.json` may be stale" in progressive
    assert progressive.index("Execution Protocol") < progressive.index("Workflow Summary")
    assert progressive.index("Execution Protocol") < progressive.index("Feedback Files")
    assert progressive.index("Feedback Files") < progressive.index("Post-Run Validation")
    assert progressive.index("Post-Run Validation") < progressive.index("Workflow Summary")
    assert progressive.index("Post-Run Validation") < progressive.index("Some drift can be acceptable, but less drift is better.")
    assert progressive.index("Some drift can be acceptable, but less drift is better.") < progressive.index("Workflow Summary")
    assert progressive.index("Feedback Files") < progressive.index("Workflow Summary")
    assert progressive.index("Workflow Summary") < progressive.index("# stage1")

    concat_stateful = render_workflow_prompt(
        workflow=workflow,
        mode="concat_stateful",
        active_index=1,
        case_blocks=blocks,
        stage_results=[{"status": "passed"}, None],
    )
    assert "# stage1" in concat_stateful
    assert "# stage2" in concat_stateful
    assert "Previous Stage Outcomes" in concat_stateful
    assert "## Stage 2/2: s2 (ACTIVE)" in concat_stateful
    assert "`WORKFLOW_STATE.json`" in concat_stateful
    assert concat_stateful.index("Execution Protocol") < concat_stateful.index("Workflow Summary")
    assert concat_stateful.index("Execution Protocol") < concat_stateful.index("Feedback Files")
    assert concat_stateful.index("Feedback Files") < concat_stateful.index("Workflow Summary")
    assert concat_stateful.index("Workflow Summary") < concat_stateful.index("All Stages")

    concat_blind = render_workflow_prompt(
        workflow=workflow,
        mode="concat_blind",
        active_index=1,
        case_blocks=blocks,
        stage_results=[{"status": "passed"}, None],
    )
    assert "# stage1" in concat_blind
    assert "# stage2" in concat_blind
    assert "Previous Stage Outcomes" not in concat_blind
    assert "Active Stage:" not in concat_blind
    assert "Total Stages: 2" in concat_blind
    assert "(ACTIVE)" not in concat_blind
    assert "`concat_blind`: all stages are shown without active-stage markers." in concat_blind
    assert "rely on submit/state files for progress" in concat_blind
    assert concat_blind.index("Execution Protocol") < concat_blind.index("Workflow Summary")
    assert concat_blind.index("Execution Protocol") < concat_blind.index("Feedback Files")
    assert concat_blind.index("Feedback Files") < concat_blind.index("Workflow Summary")
    assert concat_blind.index("Workflow Summary") < concat_blind.index("All Stages")


def test_render_workflow_prompt_disables_post_run_sweep_when_requested():
    workflow = {
        "metadata": {"name": "wf"},
        "spec": {
            "final_sweep_mode": "off",
            "stages": [
                {"id": "s1"},
            ],
        },
    }
    prompt = render_workflow_prompt(
        workflow=workflow,
        mode="progressive",
        active_index=0,
        case_blocks=["# stage1\nDo A"],
        stage_results=[None],
    )
    assert "Final stage sweep is disabled for this workflow run (`final_sweep_mode=off`)." in prompt
    assert "full verification sweep across all workflow stages" not in prompt


def test_load_workflow_spec_accepts_param_overrides():
    with tempfile.TemporaryDirectory() as tmp:
        wf_path = Path(tmp) / "wf.yaml"
        wf_path.write_text(
            """
apiVersion: benchmark/v1
kind: Workflow
metadata:
  name: unit-workflow
spec:
  prompt_mode: progressive
  stages:
    - id: stage_a
      case_ref:
        service: workflow-mock
        case: stage_seed
      param_overrides:
        expected_phase: alpha
        expected_replicas: 4
""".strip()
            + "\n",
            encoding="utf-8",
        )
        spec = load_workflow_spec(str(wf_path))
        stage = (spec.get("spec") or {}).get("stages")[0]
        assert stage.get("param_overrides") == {"expected_phase": "alpha", "expected_replicas": 4}


def test_load_workflow_spec_accepts_final_sweep_mode():
    with tempfile.TemporaryDirectory() as tmp:
        wf_path = Path(tmp) / "wf.yaml"
        wf_path.write_text(
            """
apiVersion: benchmark/v1
kind: Workflow
metadata:
  name: unit-workflow
spec:
  prompt_mode: progressive
  final_sweep_mode: off
  stages:
    - id: stage_a
      case_ref:
        service: workflow-mock
        case: stage_seed
""".strip()
            + "\n",
            encoding="utf-8",
        )
        spec = load_workflow_spec(str(wf_path))
        assert (spec.get("spec") or {}).get("final_sweep_mode") == "off"


def test_load_workflow_spec_accepts_stage_failure_mode():
    with tempfile.TemporaryDirectory() as tmp:
        wf_path = Path(tmp) / "wf.yaml"
        wf_path.write_text(
            """
apiVersion: benchmark/v1
kind: Workflow
metadata:
  name: unit-workflow
spec:
  prompt_mode: progressive
  stage_failure_mode: terminate
  stages:
    - id: stage_a
      case_ref:
        service: workflow-mock
        case: stage_seed
""".strip()
            + "\n",
            encoding="utf-8",
        )
        spec = load_workflow_spec(str(wf_path))
        assert (spec.get("spec") or {}).get("stage_failure_mode") == "terminate"


def test_load_workflow_spec_rejects_invalid_final_sweep_mode():
    with tempfile.TemporaryDirectory() as tmp:
        wf_path = Path(tmp) / "wf.yaml"
        wf_path.write_text(
            """
apiVersion: benchmark/v1
kind: Workflow
metadata:
  name: unit-workflow
spec:
  prompt_mode: progressive
  final_sweep_mode: invalid
  stages:
    - id: stage_a
      case_ref:
        service: workflow-mock
        case: stage_seed
""".strip()
            + "\n",
            encoding="utf-8",
        )
        try:
            load_workflow_spec(str(wf_path))
        except ValueError as exc:
            assert "workflow spec.final_sweep_mode must be one of" in str(exc)
        else:
            raise AssertionError("expected ValueError for invalid final_sweep_mode")


def test_load_workflow_spec_rejects_invalid_stage_failure_mode():
    with tempfile.TemporaryDirectory() as tmp:
        wf_path = Path(tmp) / "wf.yaml"
        wf_path.write_text(
            """
apiVersion: benchmark/v1
kind: Workflow
metadata:
  name: unit-workflow
spec:
  prompt_mode: progressive
  stage_failure_mode: invalid
  stages:
    - id: stage_a
      case_ref:
        service: workflow-mock
        case: stage_seed
""".strip()
            + "\n",
            encoding="utf-8",
        )
        try:
            load_workflow_spec(str(wf_path))
        except ValueError as exc:
            assert "workflow spec.stage_failure_mode must be one of" in str(exc)
        else:
            raise AssertionError("expected ValueError for invalid stage_failure_mode")


def test_workflow_submit_payload_contains_workflow_block():
    payload = workflow_submit_payload(
        base_status="failed",
        attempt=2,
        last_error="bad",
        verification_log="runs/x/verification_2.log",
        attempts_left=1,
        time_left_sec=300,
        can_retry=True,
        mode="progressive",
        stage_index=1,
        stage_total=3,
        stage_id="stage_seed",
        stage_attempt=2,
        stage_status="failed_retryable",
        continue_flag=False,
        final_flag=False,
        next_stage_id=None,
        reason="oracle_failed_retryable",
    )
    assert payload["can_retry"] is True
    wf = payload.get("workflow") or {}
    assert wf.get("enabled") is True
    assert wf.get("stage_id") == "stage_seed"
    assert wf.get("stage_status") == "failed_retryable"


def test_workflow_machine_state_payload_includes_stage_param_sources():
    payload = workflow_machine_state_payload(
        {"metadata": {"name": "wf"}, "spec": {"stages": [{"id": "s1"}]}, "path": "workflows/x.yaml"},
        rows=[
            {
                "stage": {"id": "s1"},
                "resolved_params": {"version_hint": "3.7"},
                "param_warnings": ["warn-x"],
                "param_sources": {
                    "version_hint": {
                        "kind": "stage_param_ref",
                        "stage_id": "s0",
                        "param": "to_version",
                    }
                },
                "namespace_context": {"roles": {"default": "wf-a"}},
            }
        ],
        mode="concat_stateful",
        final_sweep_mode="full",
        active_index=0,
        stage_results=[],
        solve_failed=False,
        terminal=False,
        terminal_reason="",
        ts_str_fn=lambda: "2026-01-01T00:00:00Z",
    )
    assert payload["stage_params"]["s1"]["version_hint"] == "3.7"
    assert payload["stage_param_sources"]["s1"]["version_hint"]["kind"] == "stage_param_ref"
    assert payload["stage_failure_mode"] == "continue"


def test_load_workflow_spec_accepts_stage_namespaces_and_binding():
    with tempfile.TemporaryDirectory() as tmp:
        wf_path = Path(tmp) / "wf.yaml"
        wf_path.write_text(
            """
apiVersion: benchmark/v1
kind: Workflow
metadata:
  name: unit-workflow
spec:
  prompt_mode: progressive
  namespaces:
    - cluster_a
    - cluster_b
  stages:
    - id: stage_a
      service: workflow-mock
      case: stage_seed
      namespaces: [cluster_a]
    - id: stage_b
      service: workflow-mock
      case: stage_scale
      namespaces: [cluster_a, cluster_b]
      namespace_binding:
        source: cluster_a
        target: cluster_b
""".strip()
            + "\n",
            encoding="utf-8",
        )
        spec = load_workflow_spec(str(wf_path))
        stages = (spec.get("spec") or {}).get("stages") or []
        assert (spec.get("spec") or {}).get("namespaces") == ["cluster_a", "cluster_b"]
        assert stages[1].get("namespaces") == ["cluster_a", "cluster_b"]
        assert stages[1].get("namespace_binding") == {"source": "cluster_a", "target": "cluster_b"}


def test_workflow_namespace_resolution_uses_aliases_not_stage_order():
    stage = {
        "id": "stage_migrate",
        "namespaces": ["cluster_a", "cluster_b"],
        "namespace_binding": {"source": "cluster_a", "target": "cluster_b"},
    }
    alias_map = build_alias_namespace_map(["cluster_a", "cluster_b"], run_token="wf-run", prefix="wf")
    ctx = resolve_stage_namespace_context(stage, alias_map)
    assert ctx["roles"]["source"] == alias_map["cluster_a"]
    assert ctx["roles"]["target"] == alias_map["cluster_b"]
    assert ctx["roles"]["default"] == alias_map["cluster_a"]


def test_render_case_prompt_block_hides_implicit_default_role_line_when_default_role_is_explicit():
    text = render_case_prompt_block(
        {
            "service": "rabbitmq-experiments",
            "case": "blue_green_migration",
            "detailedInstructions": "x",
            "operatorContext": "",
        },
        namespace_context={
            "default_role": "target",
            "roles": {
                "default": "ns-a",
                "source": "ns-a",
                "target": "ns-b",
            },
        },
    )
    assert "- default (target): ns-b" in text
    assert "- source: ns-a" in text
    assert "- target: ns-b" not in text
    assert "\n- default: ns-a\n" not in text


def test_render_case_prompt_block_resolves_namespace_and_param_placeholders():
    text = render_case_prompt_block(
        {
            "service": "rabbitmq-experiments",
            "case": "manual_skip_upgrade",
            "detailedInstructions": "Upgrade in ${BENCH_NAMESPACE} with ${BENCH_PARAM_TO_VERSION}.",
            "operatorContext": "source=${BENCH_NS_SOURCE} target=${BENCH_NS_TARGET}",
        },
        resolved_params={"to_version": "3.10"},
        namespace_context={
            "default_role": "source",
            "roles": {
                "default": "ns-a",
                "source": "ns-a",
                "target": "ns-b",
            },
        },
    )
    assert "Upgrade in ns-a with 3.10." in text
    assert "source=ns-a target=ns-b" in text
    assert "${BENCH_NAMESPACE}" not in text
    assert "${BENCH_NS_SOURCE}" not in text
    assert "${BENCH_NS_TARGET}" not in text
    assert "${BENCH_PARAM_TO_VERSION}" not in text


def test_workflow_namespace_hygiene_rejects_hardcoded_namespace_and_namespace_kind():
    with tempfile.TemporaryDirectory() as tmp:
        case_root = Path(tmp)
        manifest = case_root / "cm.yaml"
        manifest.write_text(
            """
apiVersion: v1
kind: ConfigMap
metadata:
  name: bad
  namespace: rabbitmq
data:
  x: "1"
---
apiVersion: v1
kind: Namespace
metadata:
  name: should-not-be-here
""".strip()
            + "\n",
            encoding="utf-8",
        )
        case_data = {
            "preOperationCommands": [
                {
                    "command": [
                        "kubectl",
                        "-n",
                        "rabbitmq",
                        "apply",
                        "-f",
                        str(manifest),
                    ]
                }
            ]
        }
        violations = workflow_namespace_hygiene_violations(
            case_data,
            case_root / "test.yaml",
        )
        assert any("hardcoded kubectl namespace" in item for item in violations)
        assert any("metadata.namespace" in item for item in violations)
        assert any("kind Namespace" in item for item in violations)
