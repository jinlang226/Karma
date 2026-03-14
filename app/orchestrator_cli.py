import shlex


def _load_orchestrator_constants():
    try:
        from app.orchestrator_core.runtime_defaults import (
            AGENT_REGISTRY,
            DEFAULT_PROXY_LISTEN,
        )
    except Exception:
        return {
            "agents": ["react"],
            "proxy_default": "127.0.0.1:8081",
        }
    agents = sorted((AGENT_REGISTRY or {}).keys()) or ["react"]
    return {
        "agents": agents,
        "proxy_default": DEFAULT_PROXY_LISTEN or "127.0.0.1:8081",
    }


def get_orchestrator_cli_options():
    constants = _load_orchestrator_constants()
    agents = constants["agents"] or ["react"]
    return {
        "choices": {
            "agents": agents,
            "sandbox": ["local", "docker"],
            "setup_timeout_mode": ["fixed", "auto"],
            "final_sweep_mode": ["inherit", "full", "off"],
            "stage_failure_mode": ["inherit", "continue", "terminate"],
            "judge_mode": ["off", "post-run", "post-batch"],
        },
        "defaults": {
            "agent": "react",
            "agent_build": False,
            "agent_tag": "",
            "agent_cleanup": False,
            "manual_start": False,
            "llm_env_file": "",
            "agent_cmd": "",
            "agent_auth_path": "",
            "agent_auth_dest": "",
            "sandbox": "docker",
            "docker_image": "",
            "source_kubeconfig": "",
            "proxy_server": constants["proxy_default"],
            "real_kubectl": "",
            "submit_timeout": 1200,
            "setup_timeout": 600,
            "setup_timeout_mode": "auto",
            "verify_timeout": 1200,
            "cleanup_timeout": 600,
            "max_attempts": "",
            "final_sweep_mode": "inherit",
            "stage_failure_mode": "inherit",
            "judge_mode": "off",
            "judge_model": "",
            "judge_base_url": "",
            "judge_timeout": 120,
            "judge_max_retries": 2,
            "judge_prompt_version": "v1",
            "judge_include_outcome": False,
            "judge_fail_open": True,
            "results_json": "",
        },
    }


def _truthy(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in ("1", "true", "yes", "on")


def _clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _parse_optional_int(value, name, errors, minimum=1):
    text = _clean_text(value)
    if not text:
        return None
    try:
        num = int(text)
    except Exception:
        errors.append(f"{name} must be an integer")
        return None
    if num < minimum:
        errors.append(f"{name} must be >= {minimum}")
        return None
    return num


def _validate_enum(value, allowed, name, errors):
    text = _clean_text(value)
    if not text:
        return ""
    if text not in allowed:
        errors.append(f"{name} must be one of: {', '.join(allowed)}")
        return ""
    return text


def _quote_token(token):
    return shlex.quote(str(token))


def _tokens_to_one_line(tokens):
    return shlex.join([str(t) for t in tokens])


def _tokens_to_multi_line(tokens):
    quoted = [_quote_token(t) for t in tokens]
    if len(quoted) <= 3:
        return " ".join(quoted)
    first = " ".join(quoted[:3])
    groups = []
    i = 3
    while i < len(quoted):
        current = quoted[i]
        if current.startswith("--") and i + 1 < len(quoted) and not quoted[i + 1].startswith("--"):
            groups.append(f"{current} {quoted[i + 1]}")
            i += 2
            continue
        groups.append(current)
        i += 1
    lines = [first]
    for group in groups:
        lines.append(f"  {group}")
    return " \\\n".join(lines)


def _build_scope_tokens(scope, errors):
    scope_type = _clean_text((scope or {}).get("type"))
    service = _clean_text((scope or {}).get("service"))
    case = _clean_text((scope or {}).get("case"))

    if scope_type == "all":
        return "batch", ["--all"]
    if scope_type == "service":
        if not service:
            errors.append("scope.service is required for service scope")
            return "batch", []
        return "batch", ["--service", service]
    if scope_type == "case":
        if not service or not case:
            errors.append("scope.service and scope.case are required for case scope")
            return "run", []
        return "run", ["--service", service, "--case", case]
    errors.append("scope.type must be one of: all, service, case")
    return "batch", []


def build_orchestrator_preview(payload):
    options = get_orchestrator_cli_options()
    defaults = options["defaults"]
    choices = options["choices"]
    errors = []
    warnings = []

    scope = (payload or {}).get("scope") or {}
    flags_in = (payload or {}).get("flags") or {}

    command, scope_tokens = _build_scope_tokens(scope, errors)

    agent = _validate_enum(flags_in.get("agent", defaults["agent"]), choices["agents"], "agent", errors) or defaults["agent"]
    sandbox = _validate_enum(flags_in.get("sandbox", defaults["sandbox"]), choices["sandbox"], "sandbox", errors) or defaults["sandbox"]
    setup_timeout_mode = _validate_enum(flags_in.get("setup_timeout_mode", defaults["setup_timeout_mode"]), choices["setup_timeout_mode"], "setup_timeout_mode", errors) or defaults["setup_timeout_mode"]
    judge_mode = _validate_enum(
        flags_in.get("judge_mode", defaults["judge_mode"]),
        choices["judge_mode"],
        "judge_mode",
        errors,
    ) or defaults["judge_mode"]

    agent_build = _truthy(flags_in.get("agent_build", defaults["agent_build"]))
    agent_cleanup = _truthy(flags_in.get("agent_cleanup", defaults["agent_cleanup"]))
    manual_start = _truthy(flags_in.get("manual_start", defaults["manual_start"]))
    judge_include_outcome = _truthy(
        flags_in.get("judge_include_outcome", defaults["judge_include_outcome"])
    )
    judge_fail_open = _truthy(flags_in.get("judge_fail_open", defaults["judge_fail_open"]))

    submit_timeout = _parse_optional_int(flags_in.get("submit_timeout", defaults["submit_timeout"]), "submit_timeout", errors)
    setup_timeout = _parse_optional_int(flags_in.get("setup_timeout", defaults["setup_timeout"]), "setup_timeout", errors)
    verify_timeout = _parse_optional_int(flags_in.get("verify_timeout", defaults["verify_timeout"]), "verify_timeout", errors)
    cleanup_timeout = _parse_optional_int(flags_in.get("cleanup_timeout", defaults["cleanup_timeout"]), "cleanup_timeout", errors)
    max_attempts = _parse_optional_int(flags_in.get("max_attempts", defaults["max_attempts"]), "max_attempts", errors)
    judge_timeout = _parse_optional_int(
        flags_in.get("judge_timeout", defaults["judge_timeout"]),
        "judge_timeout",
        errors,
    )
    judge_max_retries = _parse_optional_int(
        flags_in.get("judge_max_retries", defaults["judge_max_retries"]),
        "judge_max_retries",
        errors,
    )

    proxy_server_raw = _clean_text(flags_in.get("proxy_server", ""))

    if agent_build and sandbox != "docker":
        errors.append("--agent-build requires --sandbox docker")
    docker_image = _clean_text(flags_in.get("docker_image", defaults["docker_image"]))
    if agent_build and docker_image:
        errors.append("--agent-build cannot be used with --docker-image")

    tokens = ["python3", "orchestrator.py", command]
    tokens.extend(scope_tokens)
    tokens.extend(["--sandbox", sandbox])
    tokens.extend(["--agent", agent])

    if agent_build:
        tokens.append("--agent-build")
    if agent_cleanup:
        tokens.append("--agent-cleanup")
    if manual_start:
        tokens.append("--manual-start")

    agent_tag = _clean_text(flags_in.get("agent_tag", defaults["agent_tag"]))
    if agent_tag:
        tokens.extend(["--agent-tag", agent_tag])
    llm_env_file = _clean_text(flags_in.get("llm_env_file", defaults["llm_env_file"]))
    if llm_env_file:
        tokens.extend(["--llm-env-file", llm_env_file])
    agent_cmd = _clean_text(flags_in.get("agent_cmd", defaults["agent_cmd"]))
    if agent_cmd:
        tokens.extend(["--agent-cmd", agent_cmd])
    elif sandbox == "local":
        warnings.append("No --agent-cmd set for local sandbox; orchestrator will not launch an agent process.")

    agent_auth_path = _clean_text(flags_in.get("agent_auth_path", defaults["agent_auth_path"]))
    if agent_auth_path:
        tokens.extend(["--agent-auth-path", agent_auth_path])
    agent_auth_dest = _clean_text(flags_in.get("agent_auth_dest", defaults["agent_auth_dest"]))
    if agent_auth_dest:
        tokens.extend(["--agent-auth-dest", agent_auth_dest])

    if docker_image:
        tokens.extend(["--docker-image", docker_image])
    source_kubeconfig = _clean_text(flags_in.get("source_kubeconfig", defaults["source_kubeconfig"]))
    if source_kubeconfig:
        tokens.extend(["--source-kubeconfig", source_kubeconfig])
    if proxy_server_raw and proxy_server_raw != defaults["proxy_server"]:
        tokens.extend(["--proxy-server", proxy_server_raw])
    real_kubectl = _clean_text(flags_in.get("real_kubectl", defaults["real_kubectl"]))
    if real_kubectl:
        tokens.extend(["--real-kubectl", real_kubectl])

    if submit_timeout is not None and submit_timeout != defaults["submit_timeout"]:
        tokens.extend(["--submit-timeout", str(submit_timeout)])
    if setup_timeout is not None and setup_timeout != defaults["setup_timeout"]:
        tokens.extend(["--setup-timeout", str(setup_timeout)])
    if setup_timeout_mode and setup_timeout_mode != defaults["setup_timeout_mode"]:
        tokens.extend(["--setup-timeout-mode", setup_timeout_mode])
    if verify_timeout is not None and verify_timeout != defaults["verify_timeout"]:
        tokens.extend(["--verify-timeout", str(verify_timeout)])
    if cleanup_timeout is not None and cleanup_timeout != defaults["cleanup_timeout"]:
        tokens.extend(["--cleanup-timeout", str(cleanup_timeout)])
    if max_attempts is not None:
        tokens.extend(["--max-attempts", str(max_attempts)])

    if command == "batch":
        results_json = _clean_text(flags_in.get("results_json", defaults["results_json"]))
        if results_json:
            tokens.extend(["--results-json", results_json])

    if judge_mode and judge_mode != defaults["judge_mode"]:
        tokens.extend(["--judge-mode", judge_mode])
    judge_model = _clean_text(flags_in.get("judge_model", defaults["judge_model"]))
    if judge_model:
        tokens.extend(["--judge-model", judge_model])
    judge_base_url = _clean_text(flags_in.get("judge_base_url", defaults["judge_base_url"]))
    if judge_base_url:
        tokens.extend(["--judge-base-url", judge_base_url])
    if judge_timeout is not None and judge_timeout != defaults["judge_timeout"]:
        tokens.extend(["--judge-timeout", str(judge_timeout)])
    if judge_max_retries is not None and judge_max_retries != defaults["judge_max_retries"]:
        tokens.extend(["--judge-max-retries", str(judge_max_retries)])
    judge_prompt_version = _clean_text(
        flags_in.get("judge_prompt_version", defaults["judge_prompt_version"])
    )
    if judge_prompt_version and judge_prompt_version != defaults["judge_prompt_version"]:
        tokens.extend(["--judge-prompt-version", judge_prompt_version])
    if judge_include_outcome:
        tokens.append("--judge-include-outcome")
    if not judge_fail_open:
        tokens.append("--judge-fail-closed")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "command": command,
        "command_one_line": _tokens_to_one_line(tokens),
        "command_multi_line": _tokens_to_multi_line(tokens),
        "tokens": tokens,
    }
