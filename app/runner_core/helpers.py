import hashlib
import re
import shlex

from ..util import infer_command_timeout_seconds, parse_duration_seconds


def split_command_tokens(command):
    if command is None:
        return []
    if isinstance(command, list):
        return [str(part) for part in command]
    if isinstance(command, str):
        try:
            return shlex.split(command)
        except Exception:
            return command.split()
    return []


def label_hash(value, length=16):
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return digest[:length]


def sanitize_label_value(value):
    if value is None:
        return "na"
    text = str(value)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text)
    text = text.strip("-._")
    if not text:
        return "na"
    if len(text) > 63:
        return label_hash(text)
    if not re.match(r"^[A-Za-z0-9][-A-Za-z0-9_.]*[A-Za-z0-9]$", text):
        return label_hash(text)
    return text


def default_timeout_sec_for_command(command, stage):
    # Conservative defaults to avoid hung precondition steps. Prefer explicit timeouts
    # (`timeout_sec`) or inferred `--timeout` / `--request-timeout` flags when available.
    base = 600 if stage in ("verification", "cleanup") else 300
    tokens = split_command_tokens(command)
    if not tokens:
        return base

    # Wrapper shells: the inner script often includes explicit timeouts (which we infer).
    if tokens[0] in ("/bin/sh", "sh", "/bin/bash", "bash") and "-c" in tokens:
        return base

    if tokens[0] == "kubectl" or tokens[0].endswith("/kubectl"):
        subcmd = None
        for part in tokens[1:]:
            if part.startswith("-"):
                continue
            subcmd = part
            break
        if subcmd in ("wait", "rollout"):
            return 15 * 60
        if subcmd in ("apply", "create", "patch", "replace", "label", "annotate", "scale", "set"):
            return 120
        if subcmd == "delete":
            return 180
        if subcmd == "exec":
            return 300
        if subcmd in ("logs", "get", "describe"):
            return 120
        return base

    if tokens[0] in ("python3", "python"):
        return 10 * 60 if stage == "verification" else base

    return base


def resolve_step_timeout_sec(item, stage):
    raw = item.get("timeout_sec")
    if raw is not None:
        parsed = parse_duration_seconds(raw)
        if parsed is not None and parsed > 0:
            return int(parsed)

    inferred = infer_command_timeout_seconds(item.get("command"))
    if inferred:
        # Add a small buffer over tool-level timeouts to account for command overhead.
        return int(inferred) + 30

    return int(default_timeout_sec_for_command(item.get("command"), stage))


def count_batch_runs(rows):
    seen = set()

    def add_item(item):
        if not isinstance(item, dict):
            return
        run_dir = item.get("run_dir")
        if run_dir:
            seen.add(str(run_dir))
        runs = item.get("runs")
        if isinstance(runs, list):
            for child in runs:
                add_item(child)
        result = item.get("result")
        if isinstance(result, dict):
            add_item(result)
            nested = result.get("runs")
            if isinstance(nested, list):
                for child in nested:
                    add_item(child)

    if isinstance(rows, list):
        for row in rows:
            add_item(row)
    return len(seen)


def format_tokens_preview(tokens):
    one_line = shlex.join(tokens)
    first = shlex.join(tokens[:3])
    tail = [shlex.join([tok]) for tok in tokens[3:]]
    multi_line = first if not tail else first + " \\\n" + " \\\n".join([f"  {item}" for item in tail])
    return {
        "tokens": tokens,
        "command_one_line": one_line,
        "command_multi_line": multi_line,
    }


def _flag_text(flags, key, default_value=""):
    raw = flags.get(key, default_value)
    if raw is None:
        return ""
    return str(raw).strip()


def _flag_bool(flags, key, default_value=False):
    raw = flags.get(key, default_value)
    if isinstance(raw, bool):
        return raw
    text = str(raw or "").strip().lower()
    return text in ("1", "true", "yes", "on")


def _flag_int(flags, key, default_value):
    raw = flags.get(key, default_value)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(raw)
    except Exception:
        return None
    return value if value > 0 else None


def build_workflow_tokens(action, workflow_path, flags=None, defaults=None, choices=None, dry_run=False):
    flags = flags or {}
    defaults = defaults or {}
    choices = choices or {}
    action_text = str(action or "").strip().lower()
    if action_text != "run":
        return None, "action must be run"
    rel = str(workflow_path or "").strip()
    if not rel:
        return None, "workflow_path is required"

    tokens = ["python3", "orchestrator.py", "workflow-run", "--workflow", rel]

    sandbox = _flag_text(flags, "sandbox", defaults.get("sandbox", "docker")) or "docker"
    if sandbox not in (choices.get("sandbox") or ["local", "docker"]):
        sandbox = defaults.get("sandbox", "docker")
    tokens.extend(["--sandbox", sandbox])

    agent = _flag_text(flags, "agent", defaults.get("agent", "react")) or "react"
    if agent in (choices.get("agents") or []):
        tokens.extend(["--agent", agent])

    if _flag_bool(flags, "agent_build", defaults.get("agent_build", False)):
        tokens.append("--agent-build")
    if _flag_bool(flags, "agent_cleanup", defaults.get("agent_cleanup", False)):
        tokens.append("--agent-cleanup")
    if _flag_bool(flags, "manual_start", defaults.get("manual_start", False)):
        tokens.append("--manual-start")

    for key in (
        "agent_tag",
        "llm_env_file",
        "agent_cmd",
        "agent_auth_path",
        "agent_auth_dest",
        "docker_image",
        "source_kubeconfig",
        "real_kubectl",
    ):
        value = _flag_text(flags, key, defaults.get(key, ""))
        if value:
            tokens.extend([f"--{key.replace('_', '-')}", value])

    proxy_server_raw = _flag_text(flags, "proxy_server", "")
    if proxy_server_raw and proxy_server_raw != str(defaults.get("proxy_server", "")):
        tokens.extend(["--proxy-server", proxy_server_raw])
    for key in ("submit_timeout", "setup_timeout", "verify_timeout", "cleanup_timeout"):
        iv = _flag_int(flags, key, defaults.get(key))
        if iv:
            tokens.extend([f"--{key.replace('_', '-')}", str(iv)])

    setup_timeout_mode = _flag_text(flags, "setup_timeout_mode", defaults.get("setup_timeout_mode", "auto")) or "auto"
    if setup_timeout_mode in (choices.get("setup_timeout_mode") or ["fixed", "auto"]):
        tokens.extend(["--setup-timeout-mode", setup_timeout_mode])

    max_attempts = _flag_int(flags, "max_attempts", defaults.get("max_attempts"))
    if max_attempts:
        tokens.extend(["--max-attempts", str(max_attempts)])
    final_sweep_mode = _flag_text(
        flags,
        "final_sweep_mode",
        defaults.get("final_sweep_mode", "inherit"),
    ) or "inherit"
    if final_sweep_mode in (choices.get("final_sweep_mode") or ["inherit", "full", "off"]):
        if final_sweep_mode != "inherit":
            tokens.extend(["--final-sweep-mode", final_sweep_mode])
    stage_failure_mode = _flag_text(
        flags,
        "stage_failure_mode",
        defaults.get("stage_failure_mode", "inherit"),
    ) or "inherit"
    if stage_failure_mode in (choices.get("stage_failure_mode") or ["inherit", "continue", "terminate"]):
        if stage_failure_mode != "inherit":
            tokens.extend(["--stage-failure-mode", stage_failure_mode])

    if dry_run:
        # workflow-run has no dry-run mode; keep command valid by ignoring it.
        pass
    return tokens, None


def build_judge_tokens(target_type, target_path, dry_run=False, judge_env_file=None):
    ttype = str(target_type or "").strip().lower()
    if ttype not in ("run", "batch"):
        return None, "target_type must be run or batch"
    rel = str(target_path or "").strip()
    if not rel:
        return None, "target_path is required"
    tokens = ["python3", "scripts/judge.py", ttype]
    if ttype == "run":
        tokens.extend(["--run-dir", rel])
    else:
        tokens.extend(["--batch-dir", rel])
    if dry_run:
        tokens.append("--dry-run")
    env_file = str(judge_env_file or "").strip()
    if env_file:
        tokens.extend(["--judge-env-file", env_file])
    return tokens, None
