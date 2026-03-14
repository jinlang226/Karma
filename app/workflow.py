import json
import hashlib
import re
from copy import deepcopy
from pathlib import Path

import yaml

from .settings import ROOT
from .util import encode_case_id, is_valid_name, sanitize_name


WORKFLOW_PROMPT_MODES = ("progressive", "concat_stateful", "concat_blind")
WORKFLOW_FINAL_SWEEP_MODES = ("full", "off")
WORKFLOW_STAGE_FAILURE_MODES = ("continue", "terminate")
_DEFAULT_NAMESPACE_ALIAS = "default"
_STAGE_PARAM_REF_RE = re.compile(
    r"^\s*\$\{stages\.([a-zA-Z0-9_.-]+)\.params\.([a-zA-Z0-9_.-]+)\}\s*$"
)
_PROMPT_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z0-9_.-]+)\}")


def parse_stage_param_reference(value):
    if not isinstance(value, str):
        return None
    match = _STAGE_PARAM_REF_RE.match(value)
    if not match:
        return None
    return {
        "stage_id": match.group(1),
        "param": match.group(2),
    }


def _normalize_workflow_final_sweep_mode(value, *, default="full"):
    if value is None:
        return default
    text = str(value).strip().lower()
    aliases = {
        "full": "full",
        "on": "full",
        "true": "full",
        "1": "full",
        "off": "off",
        "false": "off",
        "no": "off",
        "0": "off",
    }
    return aliases.get(text, text)


def _normalize_workflow_stage_failure_mode(value, *, default="continue"):
    if value is None:
        return default
    text = str(value).strip().lower()
    aliases = {
        "continue": "continue",
        "always_continue": "continue",
        "advance": "continue",
        "terminate": "terminate",
        "stop": "terminate",
        "halt": "terminate",
        "fail_fast": "terminate",
        "fail-fast": "terminate",
        "abort_on_failure": "terminate",
    }
    return aliases.get(text, text)


def resolve_stage_param_overrides(
    *,
    stage,
    stage_index,
    all_stages,
    prior_stage_params,
):
    """
    Resolve ${stages.<id>.params.<name>} references in a stage's param_overrides.

    Returns:
      (resolved_overrides, warnings, param_sources)
    """
    overrides = deepcopy((stage or {}).get("param_overrides") or {})
    if not isinstance(overrides, dict):
        raise ValueError("workflow stage param_overrides must be an object")

    current_stage_id = str((stage or {}).get("id") or "").strip() or f"stage[{stage_index + 1}]"
    warnings = []
    sources = {}
    stage_order = {
        str((s or {}).get("id") or ""): idx
        for idx, s in enumerate(list(all_stages or []))
        if str((s or {}).get("id") or "").strip()
    }
    current_aliases = set(_stage_aliases_for_overlap(stage))

    for key, raw_value in list(overrides.items()):
        ref = parse_stage_param_reference(raw_value)
        if not ref:
            sources[str(key)] = {"kind": "literal"}
            continue

        ref_stage_id = ref["stage_id"]
        ref_param = ref["param"]
        ref_idx = stage_order.get(ref_stage_id)
        if ref_idx is None:
            raise ValueError(
                f"workflow stage {current_stage_id} param_overrides.{key} references unknown stage: {ref_stage_id}"
            )
        if ref_idx >= stage_index:
            raise ValueError(
                f"workflow stage {current_stage_id} param_overrides.{key} must reference an earlier stage "
                f"(got {ref_stage_id})"
            )
        if ref_stage_id not in (prior_stage_params or {}):
            raise ValueError(
                f"workflow stage {current_stage_id} param_overrides.{key} references unresolved stage params: {ref_stage_id}"
            )
        upstream_params = prior_stage_params.get(ref_stage_id) or {}
        if ref_param not in upstream_params:
            raise ValueError(
                f"workflow stage {current_stage_id} param_overrides.{key} references unknown param "
                f"'{ref_param}' on stage '{ref_stage_id}'"
            )

        overrides[key] = upstream_params.get(ref_param)
        sources[str(key)] = {
            "kind": "stage_param_ref",
            "stage_id": ref_stage_id,
            "param": ref_param,
            "expr": str(raw_value),
        }

        stale_warning = _workflow_stale_param_ref_warning(
            current_stage=stage,
            current_stage_index=stage_index,
            current_stage_id=current_stage_id,
            current_aliases=current_aliases,
            all_stages=all_stages,
            source_stage_id=ref_stage_id,
            source_stage_index=ref_idx,
            source_param=ref_param,
        )
        if stale_warning:
            warnings.append(stale_warning)

    return overrides, warnings, sources


def _stage_aliases_for_overlap(stage):
    stage_obj = stage or {}
    aliases = [str(it).strip() for it in (stage_obj.get("namespaces") or []) if str(it).strip()]
    if not aliases:
        aliases = [_DEFAULT_NAMESPACE_ALIAS]
    return aliases


def _workflow_stale_param_ref_warning(
    *,
    current_stage,
    current_stage_index,
    current_stage_id,
    current_aliases,
    all_stages,
    source_stage_id,
    source_stage_index,
    source_param,
):
    source_stage = None
    for s in all_stages or []:
        if str((s or {}).get("id") or "").strip() == str(source_stage_id):
            source_stage = s
            break
    source_aliases = set(_stage_aliases_for_overlap(source_stage))
    for mid_idx in range(int(source_stage_index) + 1, int(current_stage_index)):
        if mid_idx < 0 or mid_idx >= len(all_stages or []):
            continue
        mid_stage = (all_stages or [])[mid_idx] or {}
        mid_stage_id = str(mid_stage.get("id") or "").strip() or f"stage[{mid_idx + 1}]"
        mid_aliases = set(_stage_aliases_for_overlap(mid_stage))
        if not (mid_aliases & source_aliases and mid_aliases & current_aliases):
            continue
        mid_overrides = mid_stage.get("param_overrides") or {}
        if not isinstance(mid_overrides, dict):
            continue
        if source_param not in mid_overrides:
            continue
        return (
            f"workflow param reference may be stale: stage '{current_stage_id}' reads "
            f"stages.{source_stage_id}.params.{source_param}, but intermediate stage '{mid_stage_id}' "
            f"shares namespace aliases and also overrides '{source_param}'"
        )
    return None


def resolve_workflow_path(path_value):
    if not path_value:
        raise ValueError("workflow path is required")
    path = Path(path_value)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.exists() or not path.is_file():
        raise ValueError(f"workflow file not found: {path}")
    return path


def _normalize_workflow_spec_payload(payload, *, workflow_dir, workflow_path=None):
    if not isinstance(payload, dict):
        raise ValueError("workflow yaml must be an object")
    if str(payload.get("kind") or "").strip() != "Workflow":
        raise ValueError("workflow kind must be Workflow")
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("workflow metadata must be an object")
    workflow_name = str(metadata.get("name") or "").strip()
    if not workflow_name:
        raise ValueError("workflow metadata.name is required")
    spec = payload.get("spec") or {}
    if not isinstance(spec, dict):
        raise ValueError("workflow spec must be an object")
    prompt_mode = str(spec.get("prompt_mode") or "progressive").strip()
    if prompt_mode not in WORKFLOW_PROMPT_MODES:
        raise ValueError(
            "workflow spec.prompt_mode must be one of "
            + ", ".join(WORKFLOW_PROMPT_MODES)
        )
    final_sweep_mode = _normalize_workflow_final_sweep_mode(spec.get("final_sweep_mode"))
    if final_sweep_mode not in WORKFLOW_FINAL_SWEEP_MODES:
        raise ValueError(
            "workflow spec.final_sweep_mode must be one of "
            + ", ".join(WORKFLOW_FINAL_SWEEP_MODES)
        )
    stage_failure_mode = _normalize_workflow_stage_failure_mode(spec.get("stage_failure_mode"))
    if stage_failure_mode not in WORKFLOW_STAGE_FAILURE_MODES:
        raise ValueError(
            "workflow spec.stage_failure_mode must be one of "
            + ", ".join(WORKFLOW_STAGE_FAILURE_MODES)
        )
    stages = spec.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError("workflow spec.stages must be a non-empty list")

    raw_aliases = spec.get("namespaces")
    declared_aliases = _normalize_namespace_aliases(raw_aliases)
    aliases_required = bool(raw_aliases is not None)
    if aliases_required and not declared_aliases:
        raise ValueError("workflow spec.namespaces must be a non-empty list when provided")
    if not declared_aliases:
        declared_aliases = [_DEFAULT_NAMESPACE_ALIAS]

    normalized_stages = []
    seen_ids = set()
    for idx, raw_stage in enumerate(stages, start=1):
        if not isinstance(raw_stage, dict):
            raise ValueError(f"workflow stage[{idx}] must be an object")
        stage_id = str(raw_stage.get("id") or "").strip()
        if not stage_id:
            raise ValueError(f"workflow stage[{idx}] id is required")
        if stage_id in seen_ids:
            raise ValueError(f"workflow stage id duplicated: {stage_id}")
        seen_ids.add(stage_id)

        case_ref = raw_stage.get("case_ref") if isinstance(raw_stage.get("case_ref"), dict) else {}
        service = str(raw_stage.get("service") or case_ref.get("service") or "").strip()
        case = str(raw_stage.get("case") or case_ref.get("case") or "").strip()
        if not service or not case:
            raise ValueError(
                f"workflow stage[{idx}] service and case are required"
            )
        if not is_valid_name(service) or not is_valid_name(case):
            raise ValueError(
                f"workflow stage[{idx}] invalid service/case values: {service}/{case}"
            )

        stage_aliases = _normalize_namespace_aliases(raw_stage.get("namespaces"))
        if not stage_aliases:
            stage_aliases = [_DEFAULT_NAMESPACE_ALIAS]
        for alias in stage_aliases:
            if alias not in declared_aliases:
                raise ValueError(
                    f"workflow stage[{idx}] namespace alias not declared in spec.namespaces: {alias}"
                )

        namespace_binding_raw = raw_stage.get("namespace_binding")
        if namespace_binding_raw is None:
            namespace_binding_raw = raw_stage.get("namespaceBinding")
        if namespace_binding_raw is None:
            namespace_binding_raw = {}
        if not isinstance(namespace_binding_raw, dict):
            raise ValueError(f"workflow stage[{idx}] namespace_binding must be an object")
        namespace_binding = {}
        for role, alias_value in namespace_binding_raw.items():
            role_name = str(role or "").strip()
            alias_name = str(alias_value or "").strip()
            if not role_name:
                raise ValueError(f"workflow stage[{idx}] namespace_binding role key is empty")
            if not is_valid_name(role_name):
                raise ValueError(
                    f"workflow stage[{idx}] namespace_binding role is invalid: {role_name}"
                )
            if not alias_name:
                raise ValueError(
                    f"workflow stage[{idx}] namespace_binding role {role_name} has empty alias"
                )
            if alias_name not in stage_aliases:
                raise ValueError(
                    f"workflow stage[{idx}] namespace_binding role {role_name} references alias "
                    f"outside stage.namespaces: {alias_name}"
                )
            namespace_binding[role_name] = alias_name

        case_path_value = str(raw_stage.get("case_path") or "").strip()
        case_path = None
        if case_path_value:
            parsed_case_path = Path(case_path_value)
            if not parsed_case_path.is_absolute():
                parsed_case_path = (workflow_dir / parsed_case_path).resolve()
            if not parsed_case_path.exists() or not parsed_case_path.is_file():
                raise ValueError(
                    f"workflow stage[{idx}] case_path file not found: {parsed_case_path}"
                )
            if parsed_case_path.name != "test.yaml":
                raise ValueError(
                    f"workflow stage[{idx}] case_path must point to test.yaml: {parsed_case_path}"
                )
            try:
                parsed_case_path.relative_to(ROOT)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    f"workflow stage[{idx}] case_path must be inside repo root: {parsed_case_path}"
                ) from exc
            case_path = str(parsed_case_path)

        max_attempts = raw_stage.get("max_attempts")
        if max_attempts is not None:
            try:
                max_attempts = int(max_attempts)
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    f"workflow stage[{idx}] max_attempts must be integer"
                ) from exc
            if max_attempts <= 0:
                raise ValueError(f"workflow stage[{idx}] max_attempts must be > 0")

        param_overrides = raw_stage.get("param_overrides") or {}
        if not isinstance(param_overrides, dict):
            raise ValueError(f"workflow stage[{idx}] param_overrides must be an object")

        normalized_stages.append(
            {
                "id": stage_id,
                "index": idx,
                "service": service,
                "case": case,
                "case_ref": {"service": service, "case": case},
                "case_id": encode_case_id(service, case, "test.yaml"),
                "case_path": case_path,
                "max_attempts": max_attempts,
                "param_overrides": deepcopy(param_overrides),
                "namespaces": list(stage_aliases),
                "namespace_binding": dict(namespace_binding),
            }
        )

    normalized = {
        "path": str(workflow_path) if workflow_path else None,
        "metadata": {"name": workflow_name},
        "spec": {
            "prompt_mode": prompt_mode,
            "final_sweep_mode": final_sweep_mode,
            "stage_failure_mode": stage_failure_mode,
            "namespaces": list(declared_aliases),
            "stages": normalized_stages,
        },
    }
    return normalized


def load_workflow_spec(path):
    workflow_path = resolve_workflow_path(path)
    workflow_dir = workflow_path.parent
    try:
        payload = yaml.safe_load(workflow_path.read_text()) or {}
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"failed to parse workflow yaml: {exc}") from exc
    return _normalize_workflow_spec_payload(
        payload,
        workflow_dir=workflow_dir,
        workflow_path=workflow_path,
    )


def parse_workflow_yaml_text(text, workflow_path_hint=None):
    raw = str(text or "")
    if not raw.strip():
        raise ValueError("workflow yaml is required")
    try:
        payload = yaml.safe_load(raw) or {}
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"failed to parse workflow yaml: {exc}") from exc

    workflow_dir = ROOT
    path_value = str(workflow_path_hint or "").strip()
    normalized_path = None
    if path_value:
        hint_path = Path(path_value)
        if not hint_path.is_absolute():
            hint_path = (ROOT / hint_path).resolve()
        else:
            hint_path = hint_path.resolve()
        try:
            hint_path.relative_to(ROOT)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("workflow_path must be inside repository") from exc
        workflow_dir = hint_path.parent
        normalized_path = hint_path

    return _normalize_workflow_spec_payload(
        payload,
        workflow_dir=workflow_dir,
        workflow_path=normalized_path,
    )


def workflow_spec_to_builder_draft(workflow):
    if not isinstance(workflow, dict):
        raise ValueError("workflow spec must be an object")
    metadata = workflow.get("metadata") or {}
    spec = workflow.get("spec") or {}
    stages = []
    for stage in list(spec.get("stages") or []):
        if not isinstance(stage, dict):
            continue
        stages.append(
            {
                "id": str(stage.get("id") or "").strip(),
                "service": str(stage.get("service") or "").strip(),
                "case": str(stage.get("case") or "").strip(),
                "max_attempts": stage.get("max_attempts"),
                "namespaces": list(stage.get("namespaces") or []),
                "namespace_bindings": deepcopy(stage.get("namespace_binding") or {}),
                "param_overrides": deepcopy(stage.get("param_overrides") or {}),
            }
        )
    return {
        "metadata": {
            "name": str(metadata.get("name") or "").strip(),
        },
        "spec": {
            "prompt_mode": str(spec.get("prompt_mode") or "progressive").strip(),
            "final_sweep_mode": _normalize_workflow_final_sweep_mode(
                spec.get("final_sweep_mode"),
            ),
            "stage_failure_mode": _normalize_workflow_stage_failure_mode(
                spec.get("stage_failure_mode"),
            ),
            "namespaces": list(spec.get("namespaces") or []),
            "stages": stages,
        },
    }


def summarize_stage(stage):
    service = stage.get("service") or (stage.get("case_ref") or {}).get("service")
    case = stage.get("case") or (stage.get("case_ref") or {}).get("case")
    return {
        "id": stage.get("id"),
        "index": stage.get("index"),
        "service": service,
        "case": case,
        "case_id": stage.get("case_id"),
        "case_path": stage.get("case_path"),
        "max_attempts": stage.get("max_attempts"),
        "param_overrides": deepcopy(stage.get("param_overrides") or {}),
        "namespaces": list(stage.get("namespaces") or []),
        "namespace_binding": deepcopy(stage.get("namespace_binding") or {}),
    }


def _prompt_env_key(value):
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value)).upper()


def _prompt_render_tokens(namespace_context=None, resolved_params=None):
    ns_ctx = namespace_context if isinstance(namespace_context, dict) else {}
    ns_roles = ns_ctx.get("roles") if isinstance(ns_ctx.get("roles"), dict) else {}
    default_role = str(ns_ctx.get("default_role") or "default")
    default_ns = ns_roles.get(default_role) or ns_roles.get("default") or next(iter(ns_roles.values()), "")

    tokens = {}
    if default_ns:
        tokens["BENCH_NAMESPACE"] = str(default_ns)
    for role, ns_value in ns_roles.items():
        tokens[f"NS_{role}"] = str(ns_value)
        role_key = _prompt_env_key(role)
        if role_key:
            tokens[f"BENCH_NS_{role_key}"] = str(ns_value)

    param_values = {}
    if isinstance(resolved_params, dict) and resolved_params:
        param_values = resolved_params
    elif isinstance(ns_ctx.get("resolved_params"), dict):
        param_values = ns_ctx.get("resolved_params") or {}
    for key, value in param_values.items():
        param_key = _prompt_env_key(key)
        if not param_key:
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, sort_keys=True)
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif value is None:
            rendered = ""
        else:
            rendered = str(value)
        tokens[f"BENCH_PARAM_{param_key}"] = rendered
    return tokens


def render_prompt_placeholders(text, *, namespace_context=None, resolved_params=None):
    raw = str(text or "")
    if "${" not in raw:
        return raw
    tokens = _prompt_render_tokens(namespace_context=namespace_context, resolved_params=resolved_params)
    if not tokens:
        return raw
    return _PROMPT_PLACEHOLDER_RE.sub(lambda m: str(tokens.get(m.group(1), m.group(0))), raw)


def render_case_prompt_block(
    case_meta,
    resolved_params=None,
    param_warnings=None,
    namespace_context=None,
):
    title = f"{case_meta.get('service')}/{case_meta.get('case')}"
    ns_ctx = namespace_context if isinstance(namespace_context, dict) else {}
    params_obj = resolved_params if isinstance(resolved_params, dict) else {}
    instructions = render_prompt_placeholders(
        case_meta.get("detailedInstructions") or "",
        namespace_context=ns_ctx,
        resolved_params=params_obj,
    ).strip()
    context = render_prompt_placeholders(
        case_meta.get("operatorContext") or "",
        namespace_context=ns_ctx,
        resolved_params=params_obj,
    ).strip()
    lines = [f"# {title}", ""]
    if instructions:
        lines.append(instructions)
        lines.append("")
    if context:
        lines.append("Context")
        lines.append(context)
        lines.append("")
    ns_roles = ns_ctx.get("roles") if isinstance(ns_ctx.get("roles"), dict) else {}
    if ns_roles:
        lines.append("Namespace Scope")
        default_role = str(ns_ctx.get("default_role") or "default")
        default_ns = ns_roles.get(default_role) or ns_roles.get("default")
        hide_implicit_default_role = (
            default_role != "default"
            and "default" in ns_roles
            and default_role in ns_roles
        )
        if default_ns:
            lines.append(f"- default ({default_role}): {default_ns}")
        for role in sorted(ns_roles.keys()):
            if hide_implicit_default_role and role == "default":
                continue
            if role == default_role and default_ns:
                continue
            lines.append(f"- {role}: {ns_roles.get(role)}")
        lines.append("- operate only on the assigned namespace(s) above.")
        lines.append("")
    if params_obj:
        lines.append("Resolved Params")
        for key in sorted(params_obj.keys()):
            lines.append(f"- {key}: {json.dumps(params_obj.get(key), ensure_ascii=False)}")
        lines.append("")
    if param_warnings:
        lines.append("Param Warnings")
        for warning in param_warnings:
            lines.append(f"- {warning}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_workflow_prompt(
    workflow,
    mode,
    active_index,
    case_blocks,
    stage_results=None,
    submit_hint="Create the file `submit.signal` in this directory to submit.",
):
    """
    case_blocks: list[str], same order as workflow stages
    """
    stages = (workflow.get("spec") or {}).get("stages") or []
    if not stages:
        raise ValueError("workflow has no stages")
    if active_index < 0 or active_index >= len(stages):
        raise ValueError("active_index out of range")
    if len(case_blocks) != len(stages):
        raise ValueError("case_blocks length must match workflow stage count")

    stage_results = deepcopy(stage_results or [])
    while len(stage_results) < len(stages):
        stage_results.append(None)
    final_sweep_mode = _normalize_workflow_final_sweep_mode(
        (workflow.get("spec") or {}).get("final_sweep_mode"),
    )
    if final_sweep_mode not in WORKFLOW_FINAL_SWEEP_MODES:
        final_sweep_mode = "full"
    stage_failure_mode = _normalize_workflow_stage_failure_mode(
        (workflow.get("spec") or {}).get("stage_failure_mode"),
    )
    if stage_failure_mode not in WORKFLOW_STAGE_FAILURE_MODES:
        stage_failure_mode = "continue"

    wf_name = (workflow.get("metadata") or {}).get("name") or "workflow"
    lines = [
        f"# workflow/{wf_name}",
        "",
    ]

    lines.append("Execution Protocol")
    lines.append("- This is a multi-stage workflow; work one stage at a time.")
    lines.append("- When the current stage is ready, create `submit.signal` to submit.")
    lines.append("- After submitting, wait until `submit.signal` is removed and `submit.ack` appears.")
    lines.append("- Before `submit.ack`, `submit_result.json` may be stale from a previous attempt or stage.")
    lines.append("- Only read `submit_result.json` after `submit.ack` for the current submit.")
    lines.append("- If feedback files are not ready yet, keep waiting; do not re-submit or start a new stage action.")
    lines.append("- Use `submit_result.json` (`workflow.continue`, `can_retry`, `workflow.final`) to branch next actions.")
    lines.append("- Submit feedback may retry this stage, advance to the next stage, or end the workflow.")
    if stage_failure_mode == "terminate":
        lines.append("- Non-retryable stage failures terminate the workflow (`stage_failure_mode=terminate`).")
    else:
        lines.append("- Non-retryable stage failures may advance to the next stage (`stage_failure_mode=continue`).")
    lines.append("- On retry, re-read the updated prompt/state and fix only the current stage.")
    lines.append("")

    lines.append("Feedback Files")
    lines.append("- `submit.ack`: submit receipt marker for the current submit.")
    lines.append("- `submit_result.json`: canonical response for the latest submit attempt.")
    lines.append("- Branch on `workflow.continue`, `can_retry`, and `workflow.final` from `submit_result.json`.")
    if mode == "concat_blind":
        lines.append("- `concat_blind` hides active-stage markers; rely on submit/state files for progress.")
    elif mode == "concat_stateful":
        lines.append("- `WORKFLOW_STATE.json`: bundled workflow progress snapshot for this mode.")
    lines.append("")

    lines.append("Post-Run Validation")
    if final_sweep_mode == "off":
        lines.append("- Final stage sweep is disabled for this workflow run (`final_sweep_mode=off`).")
        lines.append("- Only per-stage runtime verification is executed during stage submissions.")
    else:
        lines.append(
            "- After final submission, the system runs a full verification sweep across all workflow stages against the final cluster state."
        )
        lines.append("- Some drift can be acceptable, but less drift is better.")
    lines.append("")

    lines.append("Workflow Summary")
    if mode == "concat_blind":
        lines.append(f"Total Stages: {len(stages)}")
    else:
        active = stages[active_index]
        lines.append(f"Active Stage: {active_index + 1}/{len(stages)} ({active.get('id')})")

    if stage_results and mode != "concat_blind":
        completed = []
        for idx, result in enumerate(stage_results):
            if not isinstance(result, dict):
                continue
            status = result.get("status")
            if not status:
                continue
            completed.append(f"- stage {idx + 1} ({stages[idx].get('id')}): {status}")
        if completed:
            lines.append("")
            lines.append("Previous Stage Outcomes")
            lines.extend(completed)
    lines.append("")

    lines.append("Prompt Mode")
    if mode == "progressive":
        lines.append("- `progressive`: only the active stage is shown.")
    elif mode == "concat_stateful":
        lines.append("- `concat_stateful`: all stages are shown and the active stage is marked.")
    else:
        lines.append("- `concat_blind`: all stages are shown without active-stage markers.")
        lines.append("- Track stage progress from submit outcomes and workflow state updates.")
    lines.append("")

    if mode == "progressive":
        lines.append(case_blocks[active_index].strip())
        lines.append("")
    else:
        lines.append("All Stages")
        lines.append("")
        for idx, block in enumerate(case_blocks, start=1):
            stage = stages[idx - 1]
            marker = "(ACTIVE)" if mode == "concat_stateful" and idx - 1 == active_index else ""
            lines.append(f"## Stage {idx}/{len(stages)}: {stage.get('id')} {marker}".rstrip())
            lines.append("")
            lines.append(block.strip())
            lines.append("")

    lines.append("Submission")
    lines.append(submit_hint)
    lines.append("- Leave `submit.signal` empty for a normal submit.")
    lines.append("- Write `{\"action\":\"cleanup\"}` in `submit.signal` to request cleanup/stop.")
    lines.append("")
    lines.append("Tools")
    lines.append("- kubectl is available in PATH (via wrapper).")
    lines.append("- KUBECONFIG is preconfigured for the benchmark proxy.")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def workflow_cache_dir(base_cache_dir, workflow_name):
    cache_root = Path(base_cache_dir)
    if not cache_root.is_absolute():
        cache_root = (ROOT / cache_root).resolve()
    return cache_root / "workflows" / workflow_name


def workflow_transition_key(from_stage, to_stage):
    return f"{from_stage}->{to_stage}"


def dump_json(path, payload):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def _normalize_namespace_aliases(raw):
    if raw is None:
        return []
    items = raw
    if isinstance(items, str):
        items = [part.strip() for part in items.split(",") if part.strip()]
    if not isinstance(items, list):
        raise ValueError("workflow namespaces must be a list of alias strings")
    aliases = []
    seen = set()
    for item in items:
        alias = str(item or "").strip()
        if not alias:
            continue
        if not is_valid_name(alias):
            raise ValueError(f"workflow namespace alias is invalid: {alias}")
        if alias in seen:
            continue
        seen.add(alias)
        aliases.append(alias)
    return aliases


def build_alias_namespace_map(aliases, run_token, prefix):
    safe_token = _dns_label(str(run_token or "run"), max_len=40)
    safe_prefix = _dns_label(str(prefix or "wf"), max_len=12)
    out = {}
    for alias in aliases or []:
        clean_alias = _dns_label(str(alias), max_len=18)
        raw = f"{safe_prefix}-{safe_token}-{clean_alias}"
        out[str(alias)] = _dns_label(raw, max_len=63)
    return out


def resolve_stage_namespace_context(stage, alias_namespace_map):
    stage_aliases = list(stage.get("namespaces") or [])
    if not stage_aliases:
        stage_aliases = [_DEFAULT_NAMESPACE_ALIAS]
    binding = stage.get("namespace_binding") or {}
    role_alias_map = {}
    if binding:
        for role, alias in binding.items():
            role_alias_map[str(role)] = str(alias)
    else:
        for alias in stage_aliases:
            role_alias_map[str(alias)] = str(alias)
    if _DEFAULT_NAMESPACE_ALIAS not in role_alias_map:
        role_alias_map[_DEFAULT_NAMESPACE_ALIAS] = stage_aliases[0]

    role_namespace_map = {}
    for role, alias in role_alias_map.items():
        ns_value = (alias_namespace_map or {}).get(alias)
        if not ns_value:
            raise ValueError(
                f"workflow stage {stage.get('id')} namespace alias is not resolved: role={role} alias={alias}"
            )
        role_namespace_map[role] = ns_value
    default_role = _DEFAULT_NAMESPACE_ALIAS if _DEFAULT_NAMESPACE_ALIAS in role_namespace_map else next(
        iter(role_namespace_map.keys())
    )
    return {
        "default_role": default_role,
        "roles": role_namespace_map,
        "aliases": {role: alias for role, alias in role_alias_map.items()},
        "stage_aliases": list(stage_aliases),
    }


def _dns_label(value, max_len=63):
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text:
        text = "ns"
    if len(text) <= max_len:
        return text
    suffix = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    head_len = max(1, max_len - len(suffix) - 1)
    head = text[:head_len].rstrip("-")
    if not head:
        head = "ns"
    return f"{head}-{suffix}"[:max_len].rstrip("-")
