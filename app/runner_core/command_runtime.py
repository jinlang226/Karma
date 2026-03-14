import hashlib
import json
import os
import re
import time
from pathlib import Path
from subprocess import PIPE, CalledProcessError, TimeoutExpired, run

from ..settings import ROOT
from ..util import command_to_string, list_requires_shell, safe_join, ts_str


def namespace_context(app):
    ctx = app.run_state.get("namespace_context")
    if not isinstance(ctx, dict):
        ctx = {}
    roles = ctx.get("roles")
    if not isinstance(roles, dict):
        roles = {}
    if not roles:
        data = app.run_state.get("data") or {}
        contract = data.get("namespace_contract")
        if not isinstance(contract, dict):
            contract = {}
        base_roles = contract.get("base_roles")
        if not isinstance(base_roles, dict):
            base_roles = contract.get("baseRoles")
        if isinstance(base_roles, dict):
            for role, namespace in base_roles.items():
                role_name = str(role or "").strip()
                ns_value = str(namespace or "").strip()
                if role_name and ns_value:
                    roles[role_name] = ns_value
        if not roles:
            base_namespace = str(contract.get("base_namespace") or contract.get("baseNamespace") or "").strip()
            if base_namespace:
                roles["default"] = base_namespace
    default_role = str(ctx.get("default_role") or "default")
    if default_role not in roles and roles:
        default_role = "default" if "default" in roles else next(iter(roles.keys()))
    return {"default_role": default_role, "roles": roles}


def namespace_env(app):
    ctx = namespace_context(app)
    roles = ctx.get("roles") or {}
    default_role = ctx.get("default_role") or "default"
    default_ns = roles.get(default_role) or roles.get("default") or (next(iter(roles.values())) if roles else "")
    env = os.environ.copy()
    if default_ns:
        env["BENCH_NAMESPACE"] = str(default_ns)
    env["BENCH_NAMESPACE_MAP"] = json.dumps(roles, sort_keys=True)
    for role, ns_value in roles.items():
        role_key = re.sub(r"[^A-Za-z0-9]+", "_", str(role)).upper()
        if role_key:
            env[f"BENCH_NS_{role_key}"] = str(ns_value)
    for key, value in _param_env_vars(app).items():
        env[key] = value
    return env


def namespace_tokens(app):
    ctx = namespace_context(app)
    roles = ctx.get("roles") or {}
    default_role = str(ctx.get("default_role") or "default")
    default_ns = roles.get(default_role) or roles.get("default") or (next(iter(roles.values())) if roles else "")
    tokens = {"BENCH_NAMESPACE": str(default_ns or "")}
    for role, ns_value in roles.items():
        tokens[f"NS_{role}"] = str(ns_value)
        role_key = re.sub(r"[^A-Za-z0-9]+", "_", str(role)).upper()
        if role_key:
            tokens[f"BENCH_NS_{role_key}"] = str(ns_value)
    tokens.update(_param_env_vars(app))
    return tokens


def _param_env_vars(app):
    raw = app.run_state.get("resolved_params")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        key_name = re.sub(r"[^A-Za-z0-9]+", "_", str(key)).upper()
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


def namespace_for_item(app, item):
    ctx = namespace_context(app)
    roles = ctx.get("roles") or {}
    if not roles:
        return None
    role = str(item.get("namespace_role") or "").strip()
    if not role:
        role = str(ctx.get("default_role") or "default")
    if role in roles:
        return str(roles.get(role))
    if "default" in roles:
        return str(roles.get("default"))
    return str(next(iter(roles.values())))


def render_command_namespace_placeholders(app, command):
    tokens = namespace_tokens(app)

    def _render_text(text):
        return re.sub(r"\$\{([A-Za-z0-9_.-]+)\}", lambda m: str(tokens.get(m.group(1), m.group(0))), text)

    if isinstance(command, list):
        return [_render_text(str(part)) for part in command]
    if command is None:
        return command
    return _render_text(str(command))


def inject_kubectl_namespace(command, namespace_value):
    if not namespace_value or not isinstance(command, list) or not command:
        return command
    first = str(command[0])
    if first != "kubectl" and not first.endswith("/kubectl"):
        return command
    out = list(command)
    if "-A" in out or "--all-namespaces" in out:
        return out
    idx = 1
    while idx < len(out):
        token = str(out[idx])
        if token in ("-n", "--namespace"):
            return out
        if token.startswith("--namespace="):
            return out
        idx += 1
    return [out[0], "-n", str(namespace_value)] + out[1:]


def prepare_exec_item(app, item):
    command = render_command_namespace_placeholders(app, item.get("command"))
    namespace_value = namespace_for_item(app, item)
    command = inject_kubectl_namespace(command, namespace_value)
    command = render_manifest_paths(app, command)
    env = namespace_env(app)
    return command, env


def render_manifest_paths(app, command):
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
    tokens = namespace_tokens(app)
    run_dir = app.run_state.get("run_dir")
    if not run_dir:
        return command
    out_dir = ROOT / run_dir / "rendered_manifests"
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
            path_obj = (ROOT / path_obj).resolve()
        if not path_obj.exists() or not path_obj.is_file():
            continue

        try:
            raw = path_obj.read_text(encoding="utf-8")
        except Exception:
            continue
        if "${" not in raw:
            continue

        rendered = re.sub(
            r"\$\{([A-Za-z0-9_.-]+)\}",
            lambda m: str(tokens.get(m.group(1), m.group(0))),
            raw,
        )
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


def render_namespace_placeholders_value(app, value):
    if isinstance(value, dict):
        return {key: render_namespace_placeholders_value(app, item) for key, item in value.items()}
    if isinstance(value, list):
        return [render_namespace_placeholders_value(app, item) for item in value]
    if isinstance(value, str):
        rendered = render_command_namespace_placeholders(app, value)
        return str(rendered)
    return value


def run_command_list(app, cmds, log_path, stage):
    if not cmds:
        return True
    log_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(cmds)

    for idx, item in enumerate(cmds, start=1):
        command, env = app._prepare_exec_item(item)
        sleep_seconds = item.get("sleep", 0) or 0
        if command is None:
            continue

        with app.run_lock:
            app.run_state["current_step"] = f"{stage}:{idx}/{total}"
        cmd_str = command_to_string(command)
        timeout_sec = app._resolve_step_timeout_sec(item, stage)
        app._append_log(log_path, f"[{ts_str()}] COMMAND {idx}/{total}: {cmd_str} (timeout={timeout_sec}s)")

        try:
            if isinstance(command, list):
                if list_requires_shell(command):
                    result = run(
                        safe_join(command),
                        cwd=ROOT,
                        text=True,
                        stdout=PIPE,
                        stderr=PIPE,
                        env=env,
                        check=True,
                        shell=True,
                        timeout=timeout_sec,
                    )
                else:
                    result = run(
                        command,
                        cwd=ROOT,
                        text=True,
                        stdout=PIPE,
                        stderr=PIPE,
                        env=env,
                        check=True,
                        timeout=timeout_sec,
                    )
            else:
                result = run(
                    str(command),
                    cwd=ROOT,
                    text=True,
                    stdout=PIPE,
                    stderr=PIPE,
                    env=env,
                    check=True,
                    shell=True,
                    timeout=timeout_sec,
                )
            if result.stdout:
                app._append_log(log_path, result.stdout.rstrip())
            if result.stderr:
                app._append_log(log_path, result.stderr.rstrip())
            app._append_log(log_path, f"[{ts_str()}] EXIT 0")
        except Exception as exc:
            if isinstance(exc, TimeoutExpired):
                if getattr(exc, "stdout", None):
                    try:
                        app._append_log(log_path, str(exc.stdout).rstrip())
                    except Exception:
                        pass
                if getattr(exc, "stderr", None):
                    try:
                        app._append_log(log_path, str(exc.stderr).rstrip())
                    except Exception:
                        pass
                app._append_log(log_path, f"[{ts_str()}] ERROR: Command timed out after {timeout_sec}s")
                with app.run_lock:
                    app.run_state["last_error"] = f"Command timed out after {timeout_sec}s"
                    app.run_state["current_step"] = None
                    app._write_meta()
                return False
            if isinstance(exc, CalledProcessError):
                if exc.stdout:
                    app._append_log(log_path, exc.stdout.rstrip())
                if exc.stderr:
                    app._append_log(log_path, exc.stderr.rstrip())
            app._append_log(log_path, f"[{ts_str()}] ERROR: {exc}")
            with app.run_lock:
                app.run_state["last_error"] = str(exc)
                app.run_state["current_step"] = None
                app._write_meta()
            return False

        if sleep_seconds:
            time.sleep(float(sleep_seconds))

    with app.run_lock:
        app.run_state["current_step"] = None
    return True


def run_command_list_stateless(app, cmds, log_path, stage="cleanup"):
    if not cmds:
        return True
    log_path.parent.mkdir(parents=True, exist_ok=True)
    total = len(cmds)
    ok = True

    for idx, item in enumerate(cmds, start=1):
        command, env = app._prepare_exec_item(item)
        sleep_seconds = item.get("sleep", 0) or 0
        if command is None:
            continue

        cmd_str = command_to_string(command)
        timeout_sec = app._resolve_step_timeout_sec(item, stage)
        app._append_log(log_path, f"[{ts_str()}] COMMAND {idx}/{total}: {cmd_str} (timeout={timeout_sec}s)")

        try:
            if isinstance(command, list):
                if list_requires_shell(command):
                    result = run(
                        safe_join(command),
                        cwd=ROOT,
                        text=True,
                        stdout=PIPE,
                        stderr=PIPE,
                        env=env,
                        check=True,
                        shell=True,
                        timeout=timeout_sec,
                    )
                else:
                    result = run(
                        command,
                        cwd=ROOT,
                        text=True,
                        stdout=PIPE,
                        stderr=PIPE,
                        env=env,
                        check=True,
                        timeout=timeout_sec,
                    )
            else:
                result = run(
                    str(command),
                    cwd=ROOT,
                    text=True,
                    stdout=PIPE,
                    stderr=PIPE,
                    env=env,
                    check=True,
                    shell=True,
                    timeout=timeout_sec,
                )
            if result.stdout:
                app._append_log(log_path, result.stdout.rstrip())
            if result.stderr:
                app._append_log(log_path, result.stderr.rstrip())
            app._append_log(log_path, f"[{ts_str()}] EXIT 0")
        except Exception as exc:
            if isinstance(exc, TimeoutExpired):
                app._append_log(log_path, f"[{ts_str()}] ERROR: Command timed out after {timeout_sec}s")
                ok = False
                continue
            if isinstance(exc, CalledProcessError):
                if exc.stdout:
                    app._append_log(log_path, exc.stdout.rstrip())
                if exc.stderr:
                    app._append_log(log_path, exc.stderr.rstrip())
            app._append_log(log_path, f"[{ts_str()}] ERROR: {exc}")
            ok = False

        if sleep_seconds:
            time.sleep(float(sleep_seconds))

    return ok
