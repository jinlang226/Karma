from __future__ import annotations

from copy import deepcopy


PLAN_SCHEMA_VERSION = "execution_plan.v1"


def _clean_text(value):
    text = str(value or "").strip()
    return text


def _positive_int(value):
    try:
        num = int(value)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return num


def _stage_payload(
    *,
    stage_id,
    case_id,
    service=None,
    case_name=None,
    case_data_override=None,
    resolved_params=None,
    param_warnings=None,
    namespace_context=None,
    workflow_namespace_aliases=None,
    max_attempts=None,
):
    return {
        "id": _clean_text(stage_id),
        "case_id": _clean_text(case_id),
        "service": _clean_text(service) or None,
        "case": _clean_text(case_name) or None,
        "case_data_override": deepcopy(case_data_override or {}),
        "resolved_params": deepcopy(resolved_params or {}),
        "param_warnings": list(param_warnings or []),
        "namespace_context": deepcopy(namespace_context or {}),
        "workflow_namespace_aliases": list(workflow_namespace_aliases or []),
        "max_attempts": _positive_int(max_attempts),
    }


def _base_plan(
    *,
    workflow_id,
    mode,
    stages,
    output_policy,
    source_kind,
    source_ref,
    compiled=None,
):
    stage_rows = list(stages or [])
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "workflow_id": _clean_text(workflow_id),
        "mode": _clean_text(mode) or "progressive",
        "stage_total": len(stage_rows),
        "output_policy": _clean_text(output_policy),
        "source": {
            "kind": _clean_text(source_kind),
            "ref": _clean_text(source_ref) or None,
        },
        "stages": stage_rows,
        "compiled": deepcopy(compiled or {}),
    }


def build_single_stage_plan(
    case_id,
    case_data,
    args,
    *,
    stage_id="stage_1",
    workflow_id=None,
    service=None,
    case_name=None,
    namespace_context=None,
    resolved_params=None,
    param_warnings=None,
    compiled_detail=None,
):
    wf_id = workflow_id or f"single:{_clean_text(case_id)}"
    stage = _stage_payload(
        stage_id=stage_id,
        case_id=case_id,
        service=service,
        case_name=case_name,
        case_data_override=case_data,
        resolved_params=resolved_params,
        param_warnings=param_warnings,
        namespace_context=namespace_context,
        max_attempts=getattr(args, "max_attempts", None),
    )
    compiled = {}
    if isinstance(compiled_detail, dict):
        compiled["case"] = deepcopy(compiled_detail)
    return _base_plan(
        workflow_id=wf_id,
        mode="single",
        stages=[stage],
        output_policy="single_mode",
        source_kind="synthetic_single_case",
        source_ref=case_id,
        compiled=compiled,
    )


def build_workflow_stage_plan(
    workflow,
    rows,
    *,
    compiled_artifact=None,
    compile_result=None,
):
    workflow = workflow if isinstance(workflow, dict) else {}
    metadata = workflow.get("metadata") or {}
    spec = workflow.get("spec") or {}
    wf_id = _clean_text(metadata.get("name")) or "workflow"
    mode = _clean_text(spec.get("prompt_mode")) or "progressive"
    stage_rows = []
    for idx, row in enumerate(list(rows or []), start=1):
        row = row if isinstance(row, dict) else {}
        stage = row.get("stage") or {}
        case_ref = stage.get("case_ref") or {}
        stage_rows.append(
            _stage_payload(
                stage_id=stage.get("id") or f"stage_{idx}",
                case_id=stage.get("case_id"),
                service=stage.get("service") or case_ref.get("service"),
                case_name=stage.get("case") or case_ref.get("case"),
                case_data_override=row.get("case_data"),
                resolved_params=row.get("resolved_params"),
                param_warnings=row.get("param_warnings"),
                namespace_context=row.get("namespace_context"),
                workflow_namespace_aliases=row.get("workflow_namespace_aliases"),
                max_attempts=stage.get("max_attempts"),
            )
        )
    compiled = {}
    if isinstance(compiled_artifact, dict):
        compiled["workflow"] = deepcopy(compiled_artifact)
    if isinstance(compile_result, dict):
        compiled["workflow_compile_result"] = deepcopy(compile_result)
    return _base_plan(
        workflow_id=wf_id,
        mode=mode,
        stages=stage_rows,
        output_policy="workflow_mode",
        source_kind="workflow_spec",
        source_ref=workflow.get("path"),
        compiled=compiled,
    )
