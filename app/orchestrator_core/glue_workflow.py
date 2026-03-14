import yaml

from app.case_params import render_case_data_with_params, resolve_case_params
from app.oracle import resolve_oracle_verify
from app.orchestrator_core.namespace_runtime import (
    attach_workflow_namespace_context as _namespace_runtime_attach_workflow_namespace_context,
)
from app.orchestrator_core.workflow_validation import (
    command_hygiene_violations as _workflow_validation_command_hygiene_violations,
    extract_f_paths as _workflow_validation_extract_f_paths,
    load_manifest_docs_for_hygiene as _workflow_validation_load_manifest_docs_for_hygiene,
    namespace_value_is_dynamic as _workflow_validation_namespace_value_is_dynamic,
    validate_stage_namespace_contract as _workflow_validation_validate_stage_namespace_contract,
    workflow_namespace_hygiene_violations as _workflow_validation_workflow_namespace_hygiene_violations,
)
from app.preconditions import normalize_precondition_units
from app.settings import ROOT
from app.test_schema import raise_for_legacy_test_yaml_keys
from app.util import command_to_string, normalize_commands
from app.workflow import (
    build_alias_namespace_map,
    resolve_stage_param_overrides,
    render_case_prompt_block,
    resolve_stage_namespace_context,
)

_PROMPT_LITERAL_PARAM_FIELDS = {"detailedInstructions", "operatorContext"}


def _load_stage_case_row(
    app,
    stage,
    *,
    param_overrides_override=None,
    extra_param_warnings=None,
    param_sources_override=None,
):
    case_id = stage.get("case_id")
    case_meta = app.get_case(case_id)
    if case_meta.get("error"):
        raise RuntimeError(
            f"workflow stage {stage.get('id')} case lookup failed: {case_meta.get('error')}"
        )
    case_path = ROOT / case_meta.get("path", "")
    if not case_path.exists():
        raise RuntimeError(f"workflow stage {stage.get('id')} case file missing: {case_path}")
    raw_case_data = yaml.safe_load(case_path.read_text()) or {}
    if raw_case_data.get("_error"):
        raise RuntimeError(raw_case_data.get("_error"))
    try:
        context = str(case_path.relative_to(ROOT))
    except Exception:
        context = str(case_path)
    raise_for_legacy_test_yaml_keys(raw_case_data, context=context)

    param_overrides = (
        param_overrides_override
        if isinstance(param_overrides_override, dict)
        else (stage.get("param_overrides") or {})
    )
    resolved_params, param_warnings = resolve_case_params(
        raw_case_data,
        param_overrides,
        allow_unresolved_top_level_keys=_PROMPT_LITERAL_PARAM_FIELDS,
    )
    merged_param_warnings = []
    if isinstance(extra_param_warnings, list):
        merged_param_warnings.extend(str(item) for item in extra_param_warnings if str(item).strip())
    merged_param_warnings.extend(param_warnings or [])
    case_data = render_case_data_with_params(
        raw_case_data,
        resolved_params,
        allow_unresolved_top_level_keys=_PROMPT_LITERAL_PARAM_FIELDS,
    )
    if case_data.get("_error"):
        raise RuntimeError(case_data.get("_error"))
    prompt_meta = dict(case_meta)
    prompt_meta["detailedInstructions"] = case_data.get("detailedInstructions", "")
    prompt_meta["operatorContext"] = case_data.get("operatorContext", "")
    namespace_contract = case_data.get("namespace_contract")
    if not isinstance(namespace_contract, dict):
        namespace_contract = {}
    required_roles = [
        str(item).strip()
        for item in (namespace_contract.get("required_roles") or [])
        if str(item).strip()
    ]
    optional_roles = [
        str(item).strip()
        for item in (namespace_contract.get("optional_roles") or [])
        if str(item).strip()
    ]
    default_role = str(namespace_contract.get("default_role") or "").strip() or "default"
    role_ownership = {}
    raw_role_ownership = namespace_contract.get("role_ownership")
    if not isinstance(raw_role_ownership, dict):
        raw_role_ownership = namespace_contract.get("roleOwnership")
    if isinstance(raw_role_ownership, dict):
        for role, owner in raw_role_ownership.items():
            role_name = str(role or "").strip()
            owner_name = str(owner or "").strip()
            if role_name and owner_name:
                role_ownership[role_name] = owner_name
    param_sources = {}
    source_overrides = (
        param_sources_override if isinstance(param_sources_override, dict) else {}
    )
    for key in (resolved_params or {}).keys():
        if key in source_overrides and isinstance(source_overrides.get(key), dict):
            param_sources[key] = dict(source_overrides.get(key) or {})
        elif key in param_overrides:
            param_sources[key] = {"kind": "literal"}
        else:
            param_sources[key] = {"kind": "default"}
    return {
        "stage": stage,
        "case_meta": case_meta,
        "case_data": case_data,
        "case_path": case_path,
        "resolved_params": resolved_params,
        "param_warnings": merged_param_warnings,
        "param_sources": param_sources,
        "namespace_contract": {
            "required_roles": required_roles,
            "optional_roles": optional_roles,
            "default_role": default_role,
            "role_ownership": role_ownership,
        },
        "prompt_block": render_case_prompt_block(
            prompt_meta,
            resolved_params=resolved_params,
            param_warnings=merged_param_warnings,
        ),
    }


def _register_workflow_case_overrides(app, workflow):
    app.clear_case_path_overrides()
    stages = (workflow.get("spec") or {}).get("stages") or []
    for stage in stages:
        case_path = stage.get("case_path")
        if not case_path:
            continue
        service = stage.get("service") or (stage.get("case_ref") or {}).get("service")
        case = stage.get("case") or (stage.get("case_ref") or {}).get("case")
        app.set_case_path_override(
            service,
            case,
            "test.yaml",
            case_path,
        )


def _validate_stage_namespace_contract(row):
    return _workflow_validation_validate_stage_namespace_contract(row)


def _namespace_value_is_dynamic(value):
    return _workflow_validation_namespace_value_is_dynamic(value)


def _extract_f_paths(tokens):
    return _workflow_validation_extract_f_paths(tokens)


def _load_manifest_docs_for_hygiene(case_path, manifest_path):
    return _workflow_validation_load_manifest_docs_for_hygiene(case_path, manifest_path)


def _command_hygiene_violations(command, case_path):
    return _workflow_validation_command_hygiene_violations(
        command,
        case_path,
        namespace_value_is_dynamic_fn=_namespace_value_is_dynamic,
        extract_f_paths_fn=_extract_f_paths,
        load_manifest_docs_for_hygiene_fn=_load_manifest_docs_for_hygiene,
        command_to_string_fn=command_to_string,
    )


def _workflow_namespace_hygiene_violations(case_data, case_path):
    return _workflow_validation_workflow_namespace_hygiene_violations(
        case_data,
        case_path,
        normalize_precondition_units_fn=normalize_precondition_units,
        normalize_commands_fn=normalize_commands,
        resolve_oracle_verify_fn=resolve_oracle_verify,
        command_hygiene_violations_fn=_command_hygiene_violations,
    )


def _resolve_workflow_rows(app, workflow):
    _register_workflow_case_overrides(app, workflow)
    spec = workflow.get("spec") or {}
    aliases = list(spec.get("namespaces") or [])
    rows = []
    stages = list((workflow.get("spec") or {}).get("stages") or [])
    prior_stage_params = {}
    for stage_index, stage in enumerate(stages):
        resolved_overrides, ref_warnings, param_sources = resolve_stage_param_overrides(
            stage=stage,
            stage_index=stage_index,
            all_stages=stages,
            prior_stage_params=prior_stage_params,
        )
        row = _load_stage_case_row(
            app,
            stage,
            param_overrides_override=resolved_overrides,
            extra_param_warnings=ref_warnings,
            param_sources_override=param_sources,
        )
        row["workflow_namespace_aliases"] = list(aliases)
        violations = _workflow_namespace_hygiene_violations(
            row.get("case_data") or {},
            row.get("case_path"),
        )
        if violations:
            raise RuntimeError(
                f"workflow stage {stage.get('id')} failed namespace hygiene checks: "
                + "; ".join(violations)
            )
        _validate_stage_namespace_contract(row)
        rows.append(row)
        stage_id = (stage or {}).get("id")
        if stage_id:
            prior_stage_params[str(stage_id)] = dict(row.get("resolved_params") or {})
    return rows


def _attach_workflow_namespace_context(rows, workflow, token, prefix):
    return _namespace_runtime_attach_workflow_namespace_context(
        rows,
        workflow,
        token,
        prefix,
        build_alias_namespace_map_fn=build_alias_namespace_map,
        resolve_stage_namespace_context_fn=resolve_stage_namespace_context,
        render_case_prompt_block_fn=render_case_prompt_block,
    )
