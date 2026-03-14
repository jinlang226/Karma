from __future__ import annotations

import re
import shlex
from pathlib import Path

import yaml

from app.oracle import resolve_oracle_verify
from app.preconditions import normalize_precondition_units
from app.util import command_to_string, normalize_commands


def validate_stage_namespace_contract(row):
    stage = row.get("stage") or {}
    contract = row.get("namespace_contract") or {}
    stage_namespaces = {
        str(alias).strip()
        for alias in (stage.get("namespaces") or [])
        if str(alias).strip()
    }
    required_roles = {
        str(role).strip()
        for role in (contract.get("required_roles") or [])
        if str(role).strip()
    }
    optional_roles = {
        str(role).strip()
        for role in (contract.get("optional_roles") or [])
        if str(role).strip()
    }
    default_role = str(contract.get("default_role") or "default").strip() or "default"
    declared_roles = set(required_roles) | set(optional_roles) | {default_role}

    role_ownership = contract.get("role_ownership")
    if role_ownership is None:
        role_ownership = contract.get("roleOwnership")
    if role_ownership is None:
        role_ownership = {}
    if not isinstance(role_ownership, dict):
        raise RuntimeError(
            f"workflow stage {stage.get('id')} namespace_contract.role_ownership must be a mapping"
        )

    ownership_keys = [
        str(role).strip()
        for role in role_ownership.keys()
        if str(role).strip()
    ]
    undeclared_keys = sorted(role for role in ownership_keys if role not in declared_roles)
    if undeclared_keys:
        raise RuntimeError(
            f"workflow stage {stage.get('id')} namespace_contract.role_ownership has undeclared role(s): "
            f"{', '.join(undeclared_keys)}"
        )
    invalid_values = []
    for role in ownership_keys:
        owner = str(role_ownership.get(role) or "").strip().lower()
        if owner not in ("framework", "case"):
            invalid_values.append(f"{role}={owner or '<empty>'}")
    if invalid_values:
        raise RuntimeError(
            f"workflow stage {stage.get('id')} namespace_contract.role_ownership has invalid value(s): "
            f"{', '.join(invalid_values)} (allowed: framework, case)"
        )

    if not required_roles:
        return
    stage_aliases = stage_namespaces
    binding = stage.get("namespace_binding") or {}
    binding_roles = {
        str(role).strip()
        for role in binding.keys()
        if str(role).strip()
    }
    available_roles = {"default"} | stage_aliases | binding_roles
    missing = sorted(role for role in required_roles if role not in available_roles)
    if missing:
        raise RuntimeError(
            f"workflow stage {stage.get('id')} is missing required namespace role(s): "
            f"{', '.join(missing)}"
        )


def normalize_role_key(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip()


def namespace_value_is_dynamic(value):
    text = str(value or "").strip()
    if not text:
        return False
    return any(marker in text for marker in ("${", "{{", "$(", "$"))


def extract_f_paths(tokens):
    paths = []
    idx = 0
    while idx < len(tokens):
        token = str(tokens[idx])
        if token in ("-f", "--filename") and idx + 1 < len(tokens):
            paths.append(str(tokens[idx + 1]))
            idx += 2
            continue
        if token.startswith("--filename="):
            paths.append(token.split("=", 1)[1])
        idx += 1
    return paths


def load_manifest_docs_for_hygiene(case_path, manifest_path):
    path = Path(manifest_path)
    if not path.is_absolute():
        path = (Path(case_path).parent / path).resolve()
    if not path.exists() or not path.is_file():
        return []
    try:
        docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except Exception:
        return []
    return [doc for doc in docs if isinstance(doc, dict)]


def command_hygiene_violations(
    command,
    case_path,
    *,
    namespace_value_is_dynamic_fn=namespace_value_is_dynamic,
    extract_f_paths_fn=extract_f_paths,
    load_manifest_docs_for_hygiene_fn=load_manifest_docs_for_hygiene,
    command_to_string_fn=command_to_string,
):
    violations = []
    if command is None:
        return violations
    tokens = []
    if isinstance(command, list):
        tokens = [str(part) for part in command]
    else:
        text = str(command)
        try:
            tokens = shlex.split(text)
        except Exception:
            tokens = text.split()
    if not tokens:
        return violations

    first = str(tokens[0])
    is_kubectl = first == "kubectl" or first.endswith("/kubectl")
    if is_kubectl:
        if "-A" in tokens or "--all-namespaces" in tokens:
            violations.append("kubectl --all-namespaces is not allowed in workflow namespace isolation")
        idx = 0
        while idx < len(tokens):
            token = str(tokens[idx])
            if token in ("-n", "--namespace"):
                if idx + 1 < len(tokens):
                    value = str(tokens[idx + 1]).strip()
                    if value and not namespace_value_is_dynamic_fn(value):
                        violations.append(
                            f"hardcoded kubectl namespace '{value}' is not allowed; use namespace_role or placeholders"
                        )
                idx += 2
                continue
            if token.startswith("--namespace="):
                value = token.split("=", 1)[1].strip()
                if value and not namespace_value_is_dynamic_fn(value):
                    violations.append(
                        f"hardcoded kubectl namespace '{value}' is not allowed; use namespace_role or placeholders"
                    )
            idx += 1

        subcmd = None
        for part in tokens[1:]:
            if part.startswith("-"):
                continue
            subcmd = str(part)
            break
        if subcmd in ("create", "delete"):
            joined = " ".join(tokens)
            if re.search(r"\bnamespaces?\b", joined):
                violations.append("creating/deleting Namespace resources is not allowed in workflow")

        for candidate in extract_f_paths_fn(tokens):
            for doc in load_manifest_docs_for_hygiene_fn(case_path, candidate):
                kind = str(doc.get("kind") or "").strip()
                if kind == "Namespace":
                    violations.append(f"manifest {candidate} defines kind Namespace (not allowed)")
                metadata = doc.get("metadata")
                if isinstance(metadata, dict) and "namespace" in metadata:
                    ns_value = metadata.get("namespace")
                    if ns_value and not namespace_value_is_dynamic_fn(ns_value):
                        violations.append(
                            f"manifest {candidate} hardcodes metadata.namespace={ns_value!r}"
                        )
    else:
        text = command_to_string_fn(command)
        if re.search(r"\bkubectl\b.*(--all-namespaces|-A)\b", text):
            violations.append("shell command uses kubectl --all-namespaces (not allowed)")
        if re.search(r"\bkubectl\b.*(?:-n\s+\S+|--namespace(?:=|\s+)\S+)", text):
            if "${" not in text and "{{" not in text and "$BENCH_NS_" not in text and "$BENCH_NAMESPACE" not in text:
                violations.append("shell command hardcodes kubectl namespace (use placeholders/env)")
        if re.search(r"\bkubectl\b.*\b(create|delete)\b.*\bnamespaces?\b", text):
            violations.append("shell command creates/deletes Namespace resources (not allowed)")
    return violations


def workflow_namespace_hygiene_violations(
    case_data,
    case_path,
    *,
    normalize_precondition_units_fn=normalize_precondition_units,
    normalize_commands_fn=normalize_commands,
    resolve_oracle_verify_fn=resolve_oracle_verify,
    command_hygiene_violations_fn=command_hygiene_violations,
):
    data = case_data or {}
    violations = []
    command_sets = []
    precondition_units = normalize_precondition_units_fn(data)
    for unit in precondition_units:
        command_sets.extend(unit.get("probe_commands") or [])
        command_sets.extend(unit.get("apply_commands") or [])
        command_sets.extend(unit.get("verify_commands") or [])
    for key in ("preOperationCommands", "cleanUpCommands"):
        command_sets.extend(normalize_commands_fn(data.get(key)))
    verify_cfg = resolve_oracle_verify_fn(data)
    command_sets.extend(verify_cfg.get("before_commands") or [])
    command_sets.extend(verify_cfg.get("commands") or [])
    command_sets.extend(verify_cfg.get("after_commands") or [])

    for item in command_sets:
        if not isinstance(item, dict):
            continue
        cmd = item.get("command")
        for violation in command_hygiene_violations_fn(cmd, case_path):
            violations.append(violation)
    seen = set()
    unique = []
    for item in violations:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique
