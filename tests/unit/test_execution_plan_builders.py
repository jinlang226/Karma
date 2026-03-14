from types import SimpleNamespace

from app.orchestrator_core.execution_plan import (
    PLAN_SCHEMA_VERSION,
    build_single_stage_plan,
    build_workflow_stage_plan,
)


def test_single_stage_plan_propagates_max_attempts_and_namespace():
    args = SimpleNamespace(max_attempts=3)
    case_data = {"type": "unit-case", "targetApp": "local"}
    namespace_context = {"default_role": "default", "roles": {"default": "ns-a", "peer": "ns-b"}}

    plan = build_single_stage_plan(
        "case-id-1",
        case_data,
        args,
        service="rabbitmq-experiments",
        case_name="manual_monitoring",
        namespace_context=namespace_context,
    )

    assert plan["schema_version"] == PLAN_SCHEMA_VERSION
    assert plan["stage_total"] == 1
    assert plan["output_policy"] == "single_mode"
    assert plan["source"]["kind"] == "synthetic_single_case"
    assert plan["source"]["ref"] == "case-id-1"
    stage = plan["stages"][0]
    assert stage["id"] == "stage_1"
    assert stage["case_id"] == "case-id-1"
    assert stage["service"] == "rabbitmq-experiments"
    assert stage["case"] == "manual_monitoring"
    assert stage["max_attempts"] == 3
    assert stage["namespace_context"]["roles"]["peer"] == "ns-b"
    assert plan["compiled"] == {}

    # ensure deep copy behavior so callers are free to mutate returned plan
    plan["stages"][0]["namespace_context"]["roles"]["peer"] = "changed"
    assert namespace_context["roles"]["peer"] == "ns-b"


def test_single_stage_plan_defaults_when_optional_values_missing():
    args = SimpleNamespace()

    plan = build_single_stage_plan(
        "case-id-2",
        {"type": "unit-case"},
        args,
    )

    assert plan["workflow_id"] == "single:case-id-2"
    assert plan["mode"] == "single"
    assert plan["stage_total"] == 1
    stage = plan["stages"][0]
    assert stage["max_attempts"] is None
    assert stage["namespace_context"] == {}


def test_workflow_stage_plan_propagates_stage_ids_combo_max_attempts_and_namespace():
    workflow = {
        "path": "workflows/demo.yaml",
        "metadata": {"name": "wf-demo"},
        "spec": {"prompt_mode": "concat_stateful"},
    }
    rows = [
        {
            "stage": {
                "id": "stage_seed",
                "case_id": "cid-seed",
                "service": "workflow-mock",
                "case": "stage_seed",
                "max_attempts": 1,
            },
            "case_data": {"type": "seed"},
            "resolved_params": {"expected_phase": "alpha"},
            "param_warnings": [],
            "namespace_context": {"default_role": "default", "roles": {"default": "wf-a"}},
            "workflow_namespace_aliases": ["cluster_a", "cluster_b"],
        },
        {
            "stage": {
                "id": "stage_scale",
                "case_id": "cid-scale",
                "case_ref": {"service": "workflow-mock", "case": "stage_scale"},
                "max_attempts": 2,
            },
            "case_data": {"type": "scale"},
            "resolved_params": {"expected_replicas": 4},
            "param_warnings": ["warning-a"],
            "namespace_context": {"default_role": "source", "roles": {"source": "wf-a", "target": "wf-b"}},
            "workflow_namespace_aliases": ["cluster_a", "cluster_b"],
        },
    ]
    plan = build_workflow_stage_plan(
        workflow,
        rows,
    )

    assert plan["schema_version"] == PLAN_SCHEMA_VERSION
    assert plan["workflow_id"] == "wf-demo"
    assert plan["mode"] == "concat_stateful"
    assert plan["output_policy"] == "workflow_mode"
    assert plan["stage_total"] == 2
    assert [stage["id"] for stage in plan["stages"]] == ["stage_seed", "stage_scale"]
    assert plan["stages"][0]["max_attempts"] == 1
    assert plan["stages"][1]["max_attempts"] == 2
    assert plan["stages"][0]["namespace_context"]["roles"]["default"] == "wf-a"
    assert plan["stages"][1]["namespace_context"]["roles"]["target"] == "wf-b"
    assert plan["stages"][0]["workflow_namespace_aliases"] == ["cluster_a", "cluster_b"]
    assert plan["compiled"] == {}


def test_workflow_stage_plan_falls_back_to_generated_stage_ids():
    workflow = {"metadata": {"name": "wf-generated"}, "spec": {"prompt_mode": "progressive"}}
    rows = [
        {"stage": {"case_id": "cid-1"}, "namespace_context": {}},
        {"stage": {"case_id": "cid-2"}, "namespace_context": {}},
    ]

    plan = build_workflow_stage_plan(workflow, rows)

    assert [stage["id"] for stage in plan["stages"]] == ["stage_1", "stage_2"]
    assert plan["stage_total"] == 2
