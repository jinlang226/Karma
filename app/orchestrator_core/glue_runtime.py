import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def _resolve_repo_root():
    try:
        return Path(
            subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
        )
    except Exception:
        return Path.cwd()


def _tail_file(path, stop_event, on_line, start_at_end=True, poll=0.5):
    offset = 0
    started = False
    while not stop_event.is_set():
        if not path.exists():
            time.sleep(poll)
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                if start_at_end and not started:
                    handle.seek(0, os.SEEK_END)
                    offset = handle.tell()
                    started = True
                else:
                    handle.seek(offset)
                    started = True
                while not stop_event.is_set():
                    line = handle.readline()
                    if not line:
                        offset = handle.tell()
                        time.sleep(poll)
                        continue
                    offset = handle.tell()
                    on_line(line.rstrip("\n"))
        except OSError:
            time.sleep(poll)


def _stream_action_trace(run_dir, stop_event):
    trace_path = Path(run_dir) / "action_trace.jsonl"

    def handle_line(line):
        if not line:
            return
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return
        command = record.get("command")
        if not command:
            return
        if isinstance(command, list):
            try:
                cmd_text = shlex.join(command)
            except Exception:
                cmd_text = " ".join(str(part) for part in command)
        else:
            cmd_text = str(command)
        ts = record.get("ts")
        if ts:
            print(f"[kubectl] {ts} {cmd_text}", flush=True)
        else:
            print(f"[kubectl] {cmd_text}", flush=True)

    _tail_file(trace_path, stop_event, handle_line, start_at_end=True, poll=0.5)


def _stream_agent_log(run_dir, stop_event):
    log_path = Path(run_dir) / "agent.log"

    def handle_line(line):
        if not line:
            return
        print(f"[agent] {line}", flush=True)

    _tail_file(log_path, stop_event, handle_line, start_at_end=True, poll=0.5)


def _make_readable_tree(root):
    for dirpath, dirnames, filenames in os.walk(root):
        try:
            os.chmod(dirpath, 0o755)
        except OSError:
            pass
        for filename in filenames:
            path = os.path.join(dirpath, filename)
            try:
                os.chmod(path, 0o644)
            except OSError:
                pass


def _prepare_agent_auth_mount(auth_path, auth_dest):
    if not auth_path:
        return None, None

    src = Path(auth_path).expanduser().resolve()
    if not src.exists():
        raise RuntimeError(f"Agent auth path not found: {src}")

    host_home = Path.home().resolve()
    try:
        rel = src.relative_to(host_home)
    except ValueError:
        rel = Path(src.name)

    container_base = Path("/home/agent")
    dest = Path(auth_dest) if auth_dest else (container_base / rel)

    temp_root = Path(tempfile.mkdtemp(prefix="bench-auth-"))
    cleanup = lambda: shutil.rmtree(temp_root, ignore_errors=True)

    if src.is_dir():
        host_dir = temp_root / rel
        host_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, host_dir, dirs_exist_ok=True)
        _make_readable_tree(host_dir)
        return {"host": str(host_dir), "container": str(dest), "cleanup": cleanup}, cleanup

    host_file = temp_root / rel
    host_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, host_file)
    try:
        os.chmod(host_file, 0o644)
    except OSError:
        pass
    return {
        "host": str(host_file.parent),
        "container": str(dest.parent),
        "cleanup": cleanup,
    }, cleanup


def _collect_case_ids(app, args):
    from app.util import encode_case_id

    if args.case_id:
        return [args.case_id]
    if args.service and args.case:
        return [encode_case_id(args.service, args.case, "test.yaml")]
    if args.service:
        return [case["id"] for case in app.list_cases(args.service)]
    if args.all:
        ids = []
        for service in app.list_services():
            ids.extend([case["id"] for case in app.list_cases(service["name"])])
        return ids
    return []
