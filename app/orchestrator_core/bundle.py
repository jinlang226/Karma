from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import yaml


def write_prompt(bundle_dir, case_meta, submit_hint, namespace_context=None):
    prompt_path = Path(bundle_dir) / "PROMPT.md"
    title = f"{case_meta['service']}/{case_meta['case']}"
    instructions = (case_meta.get("detailedInstructions") or "").strip()
    context = (case_meta.get("operatorContext") or "").strip()

    lines = [f"# {title}", ""]
    if instructions:
        lines.append(instructions)
        lines.append("")
    if context:
        lines.append("Context")
        lines.append(context)
        lines.append("")
    ns_ctx = namespace_context if isinstance(namespace_context, dict) else {}
    ns_roles = ns_ctx.get("roles") if isinstance(ns_ctx.get("roles"), dict) else {}
    if ns_roles:
        default_role = str(ns_ctx.get("default_role") or "default")
        default_ns = ns_roles.get(default_role) or ns_roles.get("default")
        hide_implicit_default_role = (
            default_role != "default"
            and "default" in ns_roles
            and default_role in ns_roles
        )
        lines.append("Namespace Scope")
        if default_ns:
            lines.append(f"- default ({default_role}): {default_ns}")
        for role in sorted(ns_roles.keys()):
            if hide_implicit_default_role and role == "default":
                continue
            if role == default_role and default_ns:
                continue
            lines.append(f"- {role}: {ns_roles.get(role)}")
        lines.append("- operate only in the namespace scope above.")
        lines.append("")

    lines.append("Submission")
    lines.append(submit_hint)
    lines.append("")
    lines.append("Tools")
    lines.append("- kubectl is available in PATH (via wrapper).")
    lines.append("- KUBECONFIG is preconfigured for the benchmark proxy.")
    lines.append("")
    prompt_path.write_text("\n".join(lines).strip() + "\n")
    return prompt_path

def write_kubectl_wrapper(bin_dir, real_kubectl):
    wrapper_path = Path(bin_dir) / "kubectl"
    wrapper_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env sh",
                "REAL_KUBECTL=${BENCHMARK_REAL_KUBECTL:-%s}" % real_kubectl,
                "TRACE_FILE=${BENCHMARK_ACTION_TRACE_LOG:-}",
                "if [ -n \"$TRACE_FILE\" ]; then",
                "  TS=$(date -u +\"%Y-%m-%dT%H:%M:%SZ\")",
                "  if command -v python3 >/dev/null 2>&1; then",
                "    python3 - \"$TRACE_FILE\" \"$TS\" \"$@\" <<'PY'",
                "import json",
                "import sys",
                "trace, ts, *args = sys.argv[1:]",
                "record = {\"ts\": ts, \"command\": [\"kubectl\"] + args}",
                "with open(trace, \"a\", encoding=\"utf-8\") as handle:",
                "    handle.write(json.dumps(record) + \"\\n\")",
                "PY",
                "  else",
                "    printf '%s\\n' \"{\\\"ts\\\":\\\"$TS\\\",\\\"command\\\":\\\"kubectl $*\\\"}\" >> \"$TRACE_FILE\"",
                "  fi",
                "fi",
                "exec \"$REAL_KUBECTL\" \"$@\"",
            ]
        )
        + "\n"
    )
    wrapper_path.chmod(0o755)
    return wrapper_path


def create_proxy_kubeconfig(output_path, source_kubeconfig, proxy_server):
    env = os.environ.copy()
    if source_kubeconfig:
        env["KUBECONFIG"] = source_kubeconfig
    raw = subprocess.check_output(
        ["kubectl", "config", "view", "--raw", "--minify", "--flatten"], text=True, env=env
    )
    data = yaml.safe_load(raw) or {}
    clusters = data.get("clusters") or []
    for cluster in clusters:
        cluster_cfg = cluster.get("cluster") or {}
        cluster_cfg["server"] = f"https://{proxy_server}"
        cluster_cfg["insecure-skip-tls-verify"] = True
        cluster_cfg.pop("certificate-authority", None)
        cluster_cfg.pop("certificate-authority-data", None)
        cluster["cluster"] = cluster_cfg
    Path(output_path).write_text(yaml.safe_dump(data, default_flow_style=False))


def detect_real_kubectl(wrapper_dir):
    paths = shutil.which("kubectl", path=os.environ.get("PATH", ""))
    if not paths:
        return None
    resolved = Path(paths).resolve()
    if wrapper_dir and resolved == (Path(wrapper_dir) / "kubectl").resolve():
        return None
    return str(resolved)


def write_env_file(
    bundle_dir,
    kubeconfig_path,
    action_trace_path,
    submit_file,
    start_file=None,
    submit_result_file=None,
    extra_env=None,
    include_workspace_bin=True,
):
    env_path = Path(bundle_dir) / "env.sh"
    lines = [
        "#!/usr/bin/env sh",
        f"export KUBECONFIG={shlex.quote(str(kubeconfig_path))}",
        f"export BENCHMARK_ACTION_TRACE_LOG={shlex.quote(str(action_trace_path))}",
        f"export BENCHMARK_SUBMIT_FILE={shlex.quote(str(submit_file))}",
        f"export BENCHMARK_START_FILE={shlex.quote(str(start_file or ''))}",
        f"export BENCHMARK_SUBMIT_RESULT_FILE={shlex.quote(str(submit_result_file or ''))}",
    ]
    if include_workspace_bin:
        lines.append("export PATH=\"$(pwd)/bin:$PATH\"")
    else:
        lines.append("export PATH=\"/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\"")
    if extra_env:
        for key in sorted(extra_env):
            value = extra_env[key]
            if value is None:
                continue
            lines.append(f"export {key}={shlex.quote(str(value))}")
    lines.append("")
    env_path.write_text("\n".join(lines))
    env_path.chmod(0o755)
    return env_path


def prepare_bundle(
    app,
    case_id,
    run_dir,
    args,
    *,
    resources_dir,
    include_workspace_bin=True,
    namespace_context=None,
    write_prompt_fn=write_prompt,
    create_proxy_kubeconfig_fn=create_proxy_kubeconfig,
    detect_real_kubectl_fn=detect_real_kubectl,
    write_kubectl_wrapper_fn=write_kubectl_wrapper,
    write_env_file_fn=write_env_file,
):
    case = app.get_case(case_id)
    if case.get("error"):
        raise RuntimeError(case["error"])

    run_dir = Path(run_dir)
    bundle_dir = run_dir / "agent_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "bin").mkdir(parents=True, exist_ok=True)

    submit_file = bundle_dir / "submit.signal"
    submit_result_file = bundle_dir / "submit_result.json"
    start_file = bundle_dir / "start.signal"

    submit_hint = f"Create the file `{submit_file.name}` in this directory to submit."

    write_prompt_fn(bundle_dir, case, submit_hint, namespace_context=namespace_context)

    kubeconfig_path = bundle_dir / "kubeconfig-proxy"
    create_proxy_kubeconfig_fn(
        kubeconfig_path,
        args.source_kubeconfig,
        args.proxy_server,
    )

    real_kubectl = args.real_kubectl
    if not real_kubectl:
        real_kubectl = detect_real_kubectl_fn(bundle_dir / "bin")
    if not real_kubectl:
        real_kubectl = "kubectl"
    write_kubectl_wrapper_fn(bundle_dir / "bin", real_kubectl)

    if include_workspace_bin:
        env_kubeconfig = "./kubeconfig-proxy"
        env_trace_path = str(run_dir / "action_trace.jsonl")
        env_submit_file = str(submit_file)
        env_submit_result = str(submit_result_file)
        env_start_file = str(start_file)
    else:
        env_kubeconfig = "/workspace/kubeconfig-proxy"
        env_trace_path = "/run/action_trace.jsonl"
        env_submit_file = "/workspace/submit.signal"
        env_submit_result = "/workspace/submit_result.json"
        env_start_file = "/workspace/start.signal"

    write_env_file_fn(
        bundle_dir,
        env_kubeconfig,
        env_trace_path,
        env_submit_file,
        start_file=env_start_file,
        submit_result_file=env_submit_result,
        extra_env=getattr(args, "_llm_env", {}),
        include_workspace_bin=include_workspace_bin,
    )

    if submit_file.exists():
        submit_file.unlink()
    if submit_result_file.exists():
        submit_result_file.unlink()
    if start_file.exists():
        start_file.unlink()

    return bundle_dir, submit_file, submit_result_file, start_file, case, real_kubectl


def ingest_agent_usage(run_dir, *, read_json_file, write_json_file, relative_path):
    run_path = Path(run_dir)
    if not run_path.exists():
        return None

    raw_path = run_path / "agent_usage_raw.json"
    raw_payload = read_json_file(raw_path)
    if not isinstance(raw_payload, dict):
        return None

    totals = raw_payload.get("totals") if isinstance(raw_payload.get("totals"), dict) else {}
    usage_payload = {
        "schema_version": "agent_usage.v1",
        "provider": raw_payload.get("provider") or "unknown",
        "source": raw_payload.get("source") or "unknown",
        "available": bool(raw_payload.get("available")),
        "totals": {
            "input_tokens": int(totals.get("input_tokens") or 0),
            "cached_input_tokens": int(totals.get("cached_input_tokens") or 0),
            "output_tokens": int(totals.get("output_tokens") or 0),
            "reasoning_output_tokens": int(totals.get("reasoning_output_tokens") or 0),
            "total_tokens": int(totals.get("total_tokens") or 0),
        },
        "model_breakdown": raw_payload.get("model_breakdown") if isinstance(raw_payload.get("model_breakdown"), dict) else {},
        "events_count": int(raw_payload.get("events_count") or 0),
        "files_scanned": int(raw_payload.get("files_scanned") or 0),
        "warnings": raw_payload.get("warnings") if isinstance(raw_payload.get("warnings"), list) else [],
    }

    usage_path = run_path / "agent_usage.json"
    if not write_json_file(usage_path, usage_payload):
        return None

    metrics_path = run_path / "external_metrics.json"
    metrics_payload = read_json_file(metrics_path)
    if not isinstance(metrics_payload, dict):
        metrics_payload = {}
    metrics_payload["agent_token_usage"] = usage_payload
    write_json_file(metrics_path, metrics_payload)

    meta_path = run_path / "meta.json"
    meta_payload = read_json_file(meta_path)
    if isinstance(meta_payload, dict):
        meta_payload["agent_usage_path"] = relative_path(usage_path)
        meta_payload["token_usage_available"] = usage_payload["available"]
        meta_payload["token_usage_total_tokens"] = usage_payload["totals"]["total_tokens"]
        meta_payload["token_usage_input_tokens"] = usage_payload["totals"]["input_tokens"]
        meta_payload["token_usage_output_tokens"] = usage_payload["totals"]["output_tokens"]
        meta_payload["token_usage_cached_input_tokens"] = usage_payload["totals"]["cached_input_tokens"]
        meta_payload["token_usage_reasoning_output_tokens"] = usage_payload["totals"]["reasoning_output_tokens"]
        meta_payload["metrics_path"] = relative_path(metrics_path)
        write_json_file(meta_path, meta_payload)

    return {
        "agent_usage_path": relative_path(usage_path),
        "metrics_path": relative_path(metrics_path),
        "token_usage_available": usage_payload["available"],
        "token_usage_total_tokens": usage_payload["totals"]["total_tokens"],
        "token_usage_input_tokens": usage_payload["totals"]["input_tokens"],
        "token_usage_output_tokens": usage_payload["totals"]["output_tokens"],
    }
