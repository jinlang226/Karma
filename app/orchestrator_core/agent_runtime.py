from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

from app.llm_config import collect_llm_env


def resolve_agent_defaults(args, repo_root, *, agent_registry, docker_build_image):
    agent_name = args.agent or "react"
    if agent_name not in agent_registry:
        raise RuntimeError(f"Unknown agent '{agent_name}'.")
    agent_meta = agent_registry[agent_name]
    dockerfile = repo_root / agent_meta["dockerfile"]
    tag = args.agent_tag or agent_meta["tag"]
    built_image = None

    if args.agent_build:
        if args.sandbox != "docker":
            raise RuntimeError("--agent-build requires --sandbox docker.")
        if args.docker_image:
            raise RuntimeError("Use --agent-build or --docker-image, not both.")
        if not dockerfile.exists():
            raise RuntimeError(f"Dockerfile not found: {dockerfile}")
        docker_build_image(tag, dockerfile, repo_root)
        built_image = tag

    if args.sandbox == "docker":
        if not args.docker_image:
            args.docker_image = tag
        if built_image:
            args.docker_image = built_image

    return built_image


def collect_agent_llm_env(args, repo_root, *, environ=None):
    env_source = environ if environ is not None else os.environ
    return collect_llm_env(
        llm_env_file=getattr(args, "llm_env_file", None),
        repo_root=repo_root,
        agent=getattr(args, "agent", "react"),
        environ=env_source,
    )


def launch_agent(bundle_dir, env, args, *, environ=None, popen=None):
    env_source = environ if environ is not None else os.environ
    process_open = popen if popen is not None else subprocess.Popen
    if args.sandbox == "docker":
        # Keep compatibility with existing arg bags.
        _docker_ctx = getattr(args, "_docker_ctx", {})
        _ = _docker_ctx
        image = args.docker_image
        if not image:
            raise RuntimeError("docker sandbox requires --docker-image")
        cmd = [
            "docker",
            "run",
            "--rm",
            "-i",
            "-v",
            f"{bundle_dir}:/workspace",
            "-v",
            f"{env['BENCHMARK_RUN_DIR']}:/run",
            "-e",
            "KUBECONFIG=/workspace/kubeconfig-proxy",
            "-e",
            "BENCHMARK_ACTION_TRACE_LOG=/run/action_trace.jsonl",
            "-e",
            f"BENCHMARK_SUBMIT_FILE=/workspace/{Path(env['BENCHMARK_SUBMIT_FILE']).name}",
            "-e",
            "BENCHMARK_START_FILE=/workspace/start.signal",
            "-e",
            "BENCHMARK_SUBMIT_RESULT_FILE=/workspace/submit_result.json",
            "-e",
            f"BENCHMARK_REAL_KUBECTL={env['BENCHMARK_REAL_KUBECTL']}",
            "-e",
            "BENCHMARK_AGENT_LOG=/run/agent.log",
            "-e",
            "BENCHMARK_USAGE_OUTPUT=/run/agent_usage_raw.json",
            "-e",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/agent/.npm-global/bin",
            "-w",
            "/workspace",
        ]
        cidfile_path = None
        run_dir = str(env.get("BENCHMARK_RUN_DIR") or "").strip()
        if run_dir:
            cidfile_path = Path(run_dir) / "agent_container.cid"
            try:
                cidfile_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                cidfile_path = None
        if cidfile_path is not None:
            cmd.extend(["--cidfile", str(cidfile_path)])
        auth_mount = getattr(args, "_agent_auth_mount", None)
        if auth_mount:
            cmd.extend(["-v", f"{auth_mount['host']}:{auth_mount['container']}:rw"])
        if env_source.get("CLAUDE_CODE_OAUTH_TOKEN"):
            cmd.extend(["-e", f"CLAUDE_CODE_OAUTH_TOKEN={env_source['CLAUDE_CODE_OAUTH_TOKEN']}"])
        cmd.append(image)
        if args.agent_cmd:
            cmd.extend(shlex.split(args.agent_cmd))
        proc = process_open(cmd)
        if cidfile_path is not None:
            try:
                setattr(proc, "_benchmark_cidfile", str(cidfile_path))
            except Exception:
                pass
        return proc

    if not args.agent_cmd:
        return None
    log_path = Path(env["BENCHMARK_RUN_DIR"]) / "agent.log"
    log_file = log_path.open("w", encoding="utf-8")
    return process_open(
        args.agent_cmd,
        shell=True,
        cwd=bundle_dir,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )


def wait_for_start_signal(path, agent_proc=None, poll=1.0):
    while True:
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
            return None
        if agent_proc:
            rc = agent_proc.poll()
            if rc is not None:
                return rc
        time.sleep(poll)


def try_read_submit_file(path):
    if not path.exists():
        return None
    try:
        content = path.read_text()
    except Exception:
        content = ""
    try:
        path.unlink()
    except Exception:
        pass
    return content


def wait_for_submit_or_agent(
    submit_file,
    agent_proc,
    timeout,
    poll=1.0,
    grace=3.0,
    read_submit_file=try_read_submit_file,
):
    start = time.time()
    exit_time = None
    exit_code = None
    while True:
        payload = read_submit_file(submit_file)
        if payload is not None:
            return payload, None
        if timeout and time.time() - start > timeout:
            return None, None
        if agent_proc:
            rc = agent_proc.poll()
            if rc is not None:
                if exit_time is None:
                    exit_time = time.time()
                    exit_code = rc
                if time.time() - exit_time >= grace:
                    return None, exit_code
        time.sleep(poll)


def terminate_agent(agent_proc, grace=3.0, *, run_cmd=subprocess.run):
    if not agent_proc:
        return
    if agent_proc.poll() is None:
        try:
            agent_proc.terminate()
        except Exception:
            pass
        try:
            agent_proc.wait(timeout=grace)
        except Exception:
            try:
                agent_proc.kill()
            except Exception:
                pass

    cidfile = getattr(agent_proc, "_benchmark_cidfile", None)
    if not cidfile:
        return
    cid_path = Path(str(cidfile))
    container_id = ""
    try:
        container_id = cid_path.read_text(encoding="utf-8").strip()
    except Exception:
        container_id = ""
    if container_id:
        try:
            run_cmd(
                ["docker", "rm", "-f", container_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass
    try:
        cid_path.unlink()
    except Exception:
        pass
