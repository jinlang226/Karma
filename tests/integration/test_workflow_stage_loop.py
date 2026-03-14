import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from app.settings import ROOT


def _cluster_ready():
    probe = subprocess.run(
        ["kubectl", "get", "ns", "--request-timeout=5s"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return probe.returncode == 0


def _write_case(case_dir, pre_marker, verify_shell, cleanup_trace):
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "resource").mkdir(parents=True, exist_ok=True)
    payload = {
        "type": "workflow-p0-case",
        "targetApp": "local",
        "numAppInstance": 0,
        "preconditionUnits": [
            {
                "id": "pre_ready",
                "probe": {"command": ["bash", "-lc", f"test -f {pre_marker}"], "timeout_sec": 5},
                "apply": {
                    "command": ["bash", "-lc", f"mkdir -p {Path(pre_marker).parent}; touch {pre_marker}"],
                    "timeout_sec": 5,
                },
                "verify": {"command": ["bash", "-lc", f"test -f {pre_marker}"], "timeout_sec": 5},
            }
        ],
        "oracle": {
            "verify": {
                "commands": [
                    {"command": ["bash", "-lc", verify_shell], "timeout_sec": 10, "sleep": 0},
                ]
            }
        },
        "cleanUpCommands": [
            {
                "command": [
                    "bash",
                    "-lc",
                    f"mkdir -p {Path(cleanup_trace).parent}; echo {case_dir.name} >> {cleanup_trace}",
                ],
                "timeout_sec": 10,
                "sleep": 0,
            }
        ],
    }
    (case_dir / "test.yaml").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_agent_script(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "import json",
                "import time",
                "from pathlib import Path",
                "",
                "submit = Path('submit.signal')",
                "result = Path('submit_result.json')",
                "last_marker = None",
                "",
                "while True:",
                "    try:",
                "        result.unlink()",
                "    except FileNotFoundError:",
                "        pass",
                "    submit.touch()",
                "    payload = None",
                "    deadline = time.time() + 600",
                "    while time.time() < deadline:",
                "        if result.exists():",
                "            try:",
                "                payload = json.loads(result.read_text(encoding='utf-8'))",
                "            except Exception:",
                "                time.sleep(0.1)",
                "                continue",
                "            wf = payload.get('workflow') or {}",
                "            marker = (payload.get('attempt'), wf.get('stage_id'), wf.get('stage_status'), wf.get('continue'), wf.get('final'), payload.get('can_retry'))",
                "            if marker != last_marker:",
                "                last_marker = marker",
                "                break",
                "        time.sleep(0.1)",
                "    if payload is None:",
                "        raise SystemExit(2)",
                "    wf = payload.get('workflow') or {}",
                "    if payload.get('can_retry'):",
                "        continue",
                "    if wf.get('continue'):",
                "        continue",
                "    if wf.get('final'):",
                "        break",
                "    break",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _parse_json_payload_from_stdout(stdout):
    marker = "[\n  {"
    pos = stdout.find(marker)
    if pos < 0:
        marker = "[{"
        pos = stdout.find(marker)
    if pos < 0:
        raise AssertionError(stdout)
    return json.loads(stdout[pos:])


def _read_jsonl(path):
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_workflow_run_stage_matrix_and_cleanup_terminal_only():
    if not _cluster_ready():
        return

    token = uuid.uuid4().hex[:8]
    service = f"workflow-p0-stage-it-{token}"
    service_dir = ROOT / "resources" / service
    wf_path = ROOT / "workflows" / f"workflow_p0_stage_{token}.yaml"
    state_dir = ROOT / ".benchmark" / "it_workflow_stage_loop" / token
    cleanup_trace = state_dir / "cleanup_trace.log"
    retry_counter = state_dir / "retry_counter.txt"
    agent_script = ROOT / ".benchmark" / "it_workflow_stage_loop" / f"agent_{token}.py"

    try:
        _write_case(
            service_dir / "stage_1_pass",
            state_dir / "pre_stage_1.ok",
            "exit 0",
            cleanup_trace,
        )
        _write_case(
            service_dir / "stage_2_retry",
            state_dir / "pre_stage_2.ok",
            (
                f"n=$(cat {retry_counter} 2>/dev/null || echo 0); "
                f"n=$((n+1)); echo \"$n\" > {retry_counter}; [ \"$n\" -ge 2 ]"
            ),
            cleanup_trace,
        )
        _write_case(
            service_dir / "stage_3_fail",
            state_dir / "pre_stage_3.ok",
            "exit 1",
            cleanup_trace,
        )
        _write_case(
            service_dir / "stage_4_final",
            state_dir / "pre_stage_4.ok",
            "exit 0",
            cleanup_trace,
        )
        _write_agent_script(agent_script)

        workflow = {
            "apiVersion": "benchmark/v1",
            "kind": "Workflow",
            "metadata": {"name": f"wf-stage-matrix-{token}"},
            "spec": {
                "prompt_mode": "progressive",
                "stages": [
                    {"id": "stage_1_pass", "service": service, "case": "stage_1_pass", "max_attempts": 1},
                    {"id": "stage_2_retry", "service": service, "case": "stage_2_retry", "max_attempts": 2},
                    {"id": "stage_3_fail", "service": service, "case": "stage_3_fail", "max_attempts": 1},
                    {"id": "stage_4_final", "service": service, "case": "stage_4_final", "max_attempts": 1},
                ],
            },
        }
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")

        cmd = [
            "python3",
            "orchestrator.py",
            "workflow-run",
            "--workflow",
            str(wf_path),
            "--sandbox",
            "local",
            "--agent-cmd",
            f"python3 {agent_script}",
            "--submit-timeout",
            "120",
            "--verify-timeout",
            "120",
            "--cleanup-timeout",
            "120",
            "--setup-timeout",
            "120",
            "--setup-timeout-mode",
            "auto",
            "--proxy-server",
            "127.0.0.1:65535",
        ]
        env = dict(os.environ)
        env["BENCHMARK_PROXY_AUTOSTART"] = "0"
        env["BENCHMARK_PROXY_CONTROL_URL"] = "127.0.0.1:1"
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout
        payload = _parse_json_payload_from_stdout(proc.stdout)
        result = (payload[0] or {}).get("result") or {}
        assert result.get("status") == "failed"

        stage_results_path = ROOT / result.get("workflow_stage_results_path")
        rows = _read_jsonl(stage_results_path)
        by_stage = {row.get("stage_id"): row for row in rows}
        assert by_stage["stage_1_pass"]["status"] == "passed"
        assert by_stage["stage_1_pass"]["attempt"] == 1
        assert by_stage["stage_2_retry"]["status"] == "passed"
        assert by_stage["stage_2_retry"]["attempt"] == 2
        assert by_stage["stage_3_fail"]["status"] == "failed_exhausted"
        assert by_stage["stage_3_fail"]["attempt"] == 1
        assert by_stage["stage_4_final"]["status"] == "passed"

        transition_path = ROOT / result.get("workflow_transition_log")
        transition_text = transition_path.read_text(encoding="utf-8")
        assert "advance stage_3_fail -> stage_4_final" in transition_text

        cleanup_lines = cleanup_trace.read_text(encoding="utf-8").splitlines()
        assert len(cleanup_lines) == 4
        assert cleanup_lines.count("stage_1_pass") == 1
        assert cleanup_lines.count("stage_2_retry") == 1
        assert cleanup_lines.count("stage_3_fail") == 1
        assert cleanup_lines.count("stage_4_final") == 1
    finally:
        shutil.rmtree(service_dir, ignore_errors=True)
        wf_path.unlink(missing_ok=True)
        agent_script.unlink(missing_ok=True)
        shutil.rmtree(state_dir, ignore_errors=True)


def test_workflow_run_fatal_on_next_stage_setup_failure():
    if not _cluster_ready():
        return

    token = uuid.uuid4().hex[:8]
    service = f"workflow-p0-fatal-it-{token}"
    service_dir = ROOT / "resources" / service
    wf_path = ROOT / "workflows" / f"workflow_p0_fatal_{token}.yaml"
    state_dir = ROOT / ".benchmark" / "it_workflow_stage_loop" / f"fatal_{token}"
    cleanup_trace = state_dir / "cleanup_trace.log"
    agent_script = ROOT / ".benchmark" / "it_workflow_stage_loop" / f"agent_fatal_{token}.py"

    try:
        _write_case(
            service_dir / "stage_ok",
            state_dir / "pre_stage_ok.ok",
            "exit 0",
            cleanup_trace,
        )
        bad_case_dir = service_dir / "stage_bad_setup"
        bad_case_dir.mkdir(parents=True, exist_ok=True)
        (bad_case_dir / "resource").mkdir(parents=True, exist_ok=True)
        bad_payload = {
            "type": "workflow-p0-bad-setup",
            "targetApp": "local",
            "numAppInstance": 0,
            "preconditionUnits": [
                {
                    "id": "will_fail",
                    "probe": {"command": ["bash", "-lc", "exit 1"], "timeout_sec": 5},
                    "apply": {"command": ["bash", "-lc", "exit 1"], "timeout_sec": 5},
                    "verify": {"command": ["bash", "-lc", "exit 1"], "timeout_sec": 5},
                }
            ],
            "oracle": {"verify": {"commands": [{"command": ["bash", "-lc", "exit 0"], "sleep": 0}]}},
            "cleanUpCommands": [
                {
                    "command": [
                        "bash",
                        "-lc",
                        f"mkdir -p {Path(cleanup_trace).parent}; echo stage_bad_setup >> {cleanup_trace}",
                    ],
                    "sleep": 0,
                }
            ],
        }
        (bad_case_dir / "test.yaml").write_text(json.dumps(bad_payload, indent=2), encoding="utf-8")
        _write_agent_script(agent_script)

        workflow = {
            "apiVersion": "benchmark/v1",
            "kind": "Workflow",
            "metadata": {"name": f"wf-fatal-{token}"},
            "spec": {
                "prompt_mode": "progressive",
                "stages": [
                    {"id": "stage_ok", "service": service, "case": "stage_ok", "max_attempts": 1},
                    {
                        "id": "stage_bad_setup",
                        "service": service,
                        "case": "stage_bad_setup",
                        "max_attempts": 1,
                    },
                ],
            },
        }
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")

        cmd = [
            "python3",
            "orchestrator.py",
            "workflow-run",
            "--workflow",
            str(wf_path),
            "--sandbox",
            "local",
            "--agent-cmd",
            f"python3 {agent_script}",
            "--submit-timeout",
            "120",
            "--verify-timeout",
            "120",
            "--cleanup-timeout",
            "120",
            "--setup-timeout",
            "120",
            "--setup-timeout-mode",
            "auto",
            "--proxy-server",
            "127.0.0.1:65535",
        ]
        env = dict(os.environ)
        env["BENCHMARK_PROXY_AUTOSTART"] = "0"
        env["BENCHMARK_PROXY_CONTROL_URL"] = "127.0.0.1:1"
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout
        payload = _parse_json_payload_from_stdout(proc.stdout)
        result = (payload[0] or {}).get("result") or {}
        assert result.get("status") == "workflow_fatal"
        assert result.get("terminal_reason") == "next_stage_setup_failed"

        rows = _read_jsonl(ROOT / result.get("workflow_stage_results_path"))
        fatal_row = next((row for row in rows if row.get("stage_id") == "stage_bad_setup"), None)
        assert fatal_row is not None
        assert fatal_row.get("status") == "fatal_error"
    finally:
        shutil.rmtree(service_dir, ignore_errors=True)
        wf_path.unlink(missing_ok=True)
        agent_script.unlink(missing_ok=True)
        shutil.rmtree(state_dir, ignore_errors=True)


def test_workflow_namespace_alias_isolation_between_stages():
    if not _cluster_ready():
        return

    token = uuid.uuid4().hex[:8]
    service = f"workflow-p0-ns-it-{token}"
    service_dir = ROOT / "resources" / service
    wf_path = ROOT / "workflows" / f"workflow_p0_ns_{token}.yaml"
    state_dir = ROOT / ".benchmark" / "it_workflow_stage_loop" / f"ns_{token}"
    cleanup_trace = state_dir / "cleanup_trace.log"
    agent_script = ROOT / ".benchmark" / "it_workflow_stage_loop" / f"agent_ns_{token}.py"

    try:
        _write_case(
            service_dir / "stage_a",
            state_dir / "pre_stage_a.ok",
            "exit 0",
            cleanup_trace,
        )
        _write_case(
            service_dir / "stage_b",
            state_dir / "pre_stage_b.ok",
            "exit 0",
            cleanup_trace,
        )
        _write_agent_script(agent_script)

        workflow = {
            "apiVersion": "benchmark/v1",
            "kind": "Workflow",
            "metadata": {"name": f"wf-ns-isolation-{token}"},
            "spec": {
                "prompt_mode": "concat_stateful",
                "namespaces": ["cluster_a", "cluster_b"],
                "stages": [
                    {
                        "id": "stage_a",
                        "service": service,
                        "case": "stage_a",
                        "max_attempts": 1,
                        "namespaces": ["cluster_a"],
                    },
                    {
                        "id": "stage_b",
                        "service": service,
                        "case": "stage_b",
                        "max_attempts": 1,
                        "namespaces": ["cluster_b"],
                    },
                ],
            },
        }
        wf_path.parent.mkdir(parents=True, exist_ok=True)
        wf_path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")

        cmd = [
            "python3",
            "orchestrator.py",
            "workflow-run",
            "--workflow",
            str(wf_path),
            "--sandbox",
            "local",
            "--agent-cmd",
            f"python3 {agent_script}",
            "--submit-timeout",
            "120",
            "--verify-timeout",
            "120",
            "--cleanup-timeout",
            "120",
            "--setup-timeout",
            "120",
            "--setup-timeout-mode",
            "auto",
            "--proxy-server",
            "127.0.0.1:65535",
        ]
        env = dict(os.environ)
        env["BENCHMARK_PROXY_AUTOSTART"] = "0"
        env["BENCHMARK_PROXY_CONTROL_URL"] = "127.0.0.1:1"
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout
        payload = _parse_json_payload_from_stdout(proc.stdout)
        result = (payload[0] or {}).get("result") or {}
        assert result.get("status") == "passed"

        state_payload = json.loads((ROOT / result.get("workflow_state_path")).read_text(encoding="utf-8"))
        stage_ns = state_payload.get("stage_namespaces") or {}
        ns_a = ((stage_ns.get("stage_a") or {}).get("default"))
        ns_b = ((stage_ns.get("stage_b") or {}).get("default"))
        assert ns_a and ns_b
        assert ns_a != ns_b
    finally:
        shutil.rmtree(service_dir, ignore_errors=True)
        wf_path.unlink(missing_ok=True)
        agent_script.unlink(missing_ok=True)
        shutil.rmtree(state_dir, ignore_errors=True)
