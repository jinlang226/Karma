from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from app.settings import ROOT


_NS_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z0-9_.-]+)\}")


def _role_env_key(role):
    return re.sub(r"[^A-Za-z0-9]+", "_", str(role)).upper()


def _namespace_tokens(namespace_context):
    ctx = namespace_context if isinstance(namespace_context, dict) else {}
    roles = ctx.get("roles") if isinstance(ctx.get("roles"), dict) else {}
    default_role = str(ctx.get("default_role") or "default")
    default_ns = roles.get(default_role) or roles.get("default") or next(iter(roles.values()), "")
    tokens = {"BENCH_NAMESPACE": str(default_ns or "")}
    for role, ns_value in roles.items():
        tokens[f"NS_{role}"] = str(ns_value)
        role_key = _role_env_key(role)
        if role_key:
            tokens[f"BENCH_NS_{role_key}"] = str(ns_value)
    tokens.update(_param_env_vars(ctx.get("resolved_params")))
    return tokens


def attach_workflow_namespace_context(
    rows,
    workflow,
    token,
    prefix,
    *,
    build_alias_namespace_map_fn,
    resolve_stage_namespace_context_fn,
    render_case_prompt_block_fn,
):
    spec = workflow.get("spec") or {}
    aliases = list(spec.get("namespaces") or [])
    alias_map = build_alias_namespace_map_fn(aliases, run_token=token, prefix=prefix)
    for row in rows or []:
        stage = row.get("stage") or {}
        stage_ctx = resolve_stage_namespace_context_fn(stage, alias_map)
        contract = row.get("namespace_contract") or {}
        default_role = str(contract.get("default_role") or "").strip()
        if default_role and default_role in (stage_ctx.get("roles") or {}):
            stage_ctx["default_role"] = default_role
        stage_ctx["resolved_params"] = row.get("resolved_params") or {}
        row["namespace_context"] = stage_ctx
        row["prompt_block"] = render_case_prompt_block_fn(
            {
                "service": stage.get("service") or (stage.get("case_ref") or {}).get("service"),
                "case": stage.get("case") or (stage.get("case_ref") or {}).get("case"),
                "detailedInstructions": (row.get("case_data") or {}).get("detailedInstructions", ""),
                "operatorContext": (row.get("case_data") or {}).get("operatorContext", ""),
            },
            resolved_params=row.get("resolved_params") or {},
            param_warnings=row.get("param_warnings") or [],
            namespace_context=stage_ctx,
        )
    return alias_map


def namespace_env(namespace_context, *, environ=None):
    base = dict(environ) if environ is not None else os.environ.copy()
    base.update(namespace_env_vars(namespace_context))
    return base


def namespace_env_vars(namespace_context, default_ns=None, roles=None):
    ctx = namespace_context if isinstance(namespace_context, dict) else {}
    role_map = roles if isinstance(roles, dict) else (ctx.get("roles") if isinstance(ctx.get("roles"), dict) else {})
    if default_ns is None:
        default_role = str(ctx.get("default_role") or "default")
        default_ns = role_map.get(default_role) or role_map.get("default") or next(iter(role_map.values()), "")
    out = {}
    if default_ns:
        out["BENCH_NAMESPACE"] = str(default_ns)
    out["BENCH_NAMESPACE_MAP"] = json.dumps(role_map, sort_keys=True)
    for role, ns_value in role_map.items():
        role_key = _role_env_key(role)
        if role_key:
            out[f"BENCH_NS_{role_key}"] = str(ns_value)
    out.update(_param_env_vars(ctx.get("resolved_params")))
    return out


def _param_env_vars(resolved_params):
    if not isinstance(resolved_params, dict):
        return {}
    out = {}
    for key, value in resolved_params.items():
        key_name = _role_env_key(key)
        if not key_name:
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, sort_keys=True)
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif value is None:
            rendered = ""
        else:
            rendered = str(value)
        out[f"BENCH_PARAM_{key_name}"] = rendered
    return out


def namespace_value_for_item(item, namespace_context):
    ctx = namespace_context if isinstance(namespace_context, dict) else {}
    roles = ctx.get("roles") if isinstance(ctx.get("roles"), dict) else {}
    if not roles:
        return None
    role = str(item.get("namespace_role") or item.get("namespaceRole") or "").strip()
    if not role:
        role = str(ctx.get("default_role") or "default")
    if role in roles:
        return str(roles.get(role))
    return str(roles.get("default") or next(iter(roles.values())))


def render_command_namespace_placeholders(command, namespace_context):
    tokens = _namespace_tokens(namespace_context)

    def render_text(text):
        return _NS_PLACEHOLDER_RE.sub(lambda m: str(tokens.get(m.group(1), m.group(0))), text)

    if isinstance(command, list):
        return [render_text(str(part)) for part in command]
    if command is None:
        return command
    return render_text(str(command))


def inject_kubectl_namespace(command, namespace_value):
    if not namespace_value or not isinstance(command, list) or not command:
        return command
    first = str(command[0])
    if first != "kubectl" and not first.endswith("/kubectl"):
        return command
    out = list(command)
    if "-A" in out or "--all-namespaces" in out:
        return out
    has_ns = False
    idx = 1
    while idx < len(out):
        token = str(out[idx])
        if token in ("-n", "--namespace"):
            if idx + 1 < len(out):
                has_ns = True
            break
        if token.startswith("--namespace="):
            has_ns = True
            break
        idx += 1
    if has_ns:
        return out
    return [out[0], "-n", str(namespace_value)] + out[1:]


def render_manifest_paths(command, namespace_context, *, render_dir=None, root=ROOT):
    if not isinstance(command, list) or not command:
        return command
    first = str(command[0])
    if first != "kubectl" and not first.endswith("/kubectl"):
        return command

    filename_indexes = []
    idx = 0
    while idx < len(command):
        token = str(command[idx])
        if token in ("-f", "--filename") and idx + 1 < len(command):
            filename_indexes.append(idx + 1)
            idx += 2
            continue
        if token.startswith("--filename="):
            filename_indexes.append(idx)
        idx += 1
    if not filename_indexes:
        return command

    rendered_command = list(command)
    changed = False
    tokens = _namespace_tokens(namespace_context)

    out_dir = Path(render_dir) if render_dir else None
    if out_dir is None:
        out_dir = Path(root) / "runs" / ".rendered_manifests"
    out_dir.mkdir(parents=True, exist_ok=True)

    for pos in filename_indexes:
        token = str(rendered_command[pos])
        inline_prefix = None
        path_value = token
        if token.startswith("--filename="):
            inline_prefix = "--filename="
            path_value = token.split("=", 1)[1]
        if path_value in ("-", "/dev/stdin"):
            continue

        path_obj = Path(path_value)
        if not path_obj.is_absolute():
            path_obj = (Path(root) / path_obj).resolve()
        if not path_obj.exists() or not path_obj.is_file():
            continue

        try:
            raw = path_obj.read_text(encoding="utf-8")
        except Exception:
            continue
        if "${" not in raw:
            continue

        rendered = _NS_PLACEHOLDER_RE.sub(lambda m: str(tokens.get(m.group(1), m.group(0))), raw)
        if rendered == raw:
            continue

        digest = hashlib.sha1((str(path_obj) + rendered).encode("utf-8")).hexdigest()[:10]
        out_path = out_dir / f"{path_obj.stem}.{digest}{path_obj.suffix or '.yaml'}"
        out_path.write_text(rendered, encoding="utf-8")

        replacement = str(out_path)
        if inline_prefix:
            replacement = f"{inline_prefix}{replacement}"
        rendered_command[pos] = replacement
        changed = True

    return rendered_command if changed else command


def prepare_exec_command(item, namespace_context, *, render_dir=None, root=ROOT, environ=None):
    command = item.get("command")
    rendered = render_command_namespace_placeholders(command, namespace_context)
    ns_value = namespace_value_for_item(item, namespace_context)
    rendered = inject_kubectl_namespace(rendered, ns_value)
    rendered = render_manifest_paths(rendered, namespace_context, render_dir=render_dir, root=root)
    env = namespace_env(namespace_context, environ=environ)
    return rendered, env
