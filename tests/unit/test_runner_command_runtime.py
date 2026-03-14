import json
import shutil
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import patch

from app.runner import BenchmarkApp
from app.settings import ROOT, RUNS_DIR


def _make_app():
    with patch.object(BenchmarkApp, "_check_cluster", return_value=(True, "ok")):
        return BenchmarkApp()


def test_namespace_context_falls_back_to_contract_defaults():
    app = _make_app()
    app.run_state = app._empty_run_state()

    app.run_state["data"] = {
        "namespace_contract": {
            "base_roles": {
                "target": "cmp-r4-target",
                "source": "cmp-r4-source",
            }
        }
    }
    ctx = app._namespace_context()
    assert ctx["roles"] == {
        "target": "cmp-r4-target",
        "source": "cmp-r4-source",
    }
    assert ctx["default_role"] == "target"

    app.run_state["namespace_context"] = None
    app.run_state["data"] = {
        "namespace_contract": {
            "base_namespace": "cmp-r4-default",
        }
    }
    ctx = app._namespace_context()
    assert ctx["roles"] == {"default": "cmp-r4-default"}
    assert ctx["default_role"] == "default"


def test_prepare_exec_item_renders_placeholders_and_injects_namespace():
    app = _make_app()
    app.run_state = app._empty_run_state()
    app.run_state["namespace_context"] = {
        "default_role": "target",
        "roles": {
            "target": "cmp-r4-target",
            "peer": "cmp-r4-peer",
        },
    }
    app.run_state["resolved_params"] = {
        "cluster_prefix": "rabbitmq-a",
        "replicas": 3,
    }
    item = {
        "namespace_role": "target",
        "command": ["kubectl", "get", "pods", "-l", "peer=${NS_peer}", "pod=${BENCH_PARAM_CLUSTER_PREFIX}-0"],
    }

    command, env = app._prepare_exec_item(item)
    assert command == [
        "kubectl",
        "-n",
        "cmp-r4-target",
        "get",
        "pods",
        "-l",
        "peer=cmp-r4-peer",
        "pod=rabbitmq-a-0",
    ]
    assert env["BENCH_NAMESPACE"] == "cmp-r4-target"
    assert env["BENCH_NS_TARGET"] == "cmp-r4-target"
    assert env["BENCH_NS_PEER"] == "cmp-r4-peer"
    assert env["BENCH_PARAM_CLUSTER_PREFIX"] == "rabbitmq-a"
    assert env["BENCH_PARAM_REPLICAS"] == "3"
    assert json.loads(env["BENCH_NAMESPACE_MAP"]) == {
        "peer": "cmp-r4-peer",
        "target": "cmp-r4-target",
    }


def test_inject_kubectl_namespace_respects_existing_namespace_flags():
    app = _make_app()
    assert app._inject_kubectl_namespace(["kubectl", "get", "pods", "-n", "x"], "cmp-r4") == [
        "kubectl",
        "get",
        "pods",
        "-n",
        "x",
    ]
    assert app._inject_kubectl_namespace(["kubectl", "get", "pods", "--namespace=x"], "cmp-r4") == [
        "kubectl",
        "get",
        "pods",
        "--namespace=x",
    ]
    assert app._inject_kubectl_namespace(["kubectl", "get", "pods", "-A"], "cmp-r4") == [
        "kubectl",
        "get",
        "pods",
        "-A",
    ]
    assert app._inject_kubectl_namespace(["kubectl", "get", "pods", "--all-namespaces"], "cmp-r4") == [
        "kubectl",
        "get",
        "pods",
        "--all-namespaces",
    ]
    assert app._inject_kubectl_namespace(["echo", "hello"], "cmp-r4") == ["echo", "hello"]


def test_render_manifest_paths_materializes_namespace_rendered_copy():
    app = _make_app()
    run_root = RUNS_DIR / "unit_runner_r4_render_manifest"
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    manifest = run_root / "input.yaml"
    manifest.write_text(
        "\n".join(
            [
                "apiVersion: v1",
                "kind: ConfigMap",
                "metadata:",
                "  name: unit-r4",
                "  namespace: ${BENCH_NAMESPACE}",
                "data:",
                "  cluster: ${BENCH_PARAM_CLUSTER_PREFIX}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    app.run_state = app._empty_run_state()
    app.run_state["run_dir"] = str(run_root.relative_to(ROOT))
    app.run_state["namespace_context"] = {
        "default_role": "default",
        "roles": {"default": "cmp-r4-default"},
    }
    app.run_state["resolved_params"] = {"cluster_prefix": "rabbitmq-a"}
    command = ["kubectl", "apply", "-f", str(manifest.relative_to(ROOT))]

    rendered = app._render_manifest_paths(command)

    try:
        assert rendered != command
        rendered_path = Path(rendered[3])
        assert rendered_path.exists()
        assert rendered_path.parent == run_root / "rendered_manifests"
        text = rendered_path.read_text(encoding="utf-8")
        assert "namespace: cmp-r4-default" in text
        assert "cluster: rabbitmq-a" in text
    finally:
        shutil.rmtree(run_root, ignore_errors=True)


def test_run_command_list_timeout_sets_last_error_and_clears_step():
    app = _make_app()
    run_root = RUNS_DIR / "unit_runner_r4_timeout"
    shutil.rmtree(run_root, ignore_errors=True)
    run_root.mkdir(parents=True, exist_ok=True)
    app.run_state = app._empty_run_state()
    app.run_state.update(
        {
            "status": "setup_running",
            "run_dir": str(run_root.relative_to(ROOT)),
        }
    )
    log_path = run_root / "setup.log"
    cmds = [{"command": ["bash", "-lc", "echo never"], "timeout_sec": 7, "sleep": 0}]
    timeout_exc = TimeoutExpired(cmd=["bash", "-lc", "echo never"], timeout=7, output="stdout", stderr="stderr")

    with patch("app.runner_core.command_runtime.run", side_effect=timeout_exc):
        ok = app._run_command_list(cmds, log_path, stage="setup")

    try:
        assert ok is False
        assert app.run_state["last_error"] == "Command timed out after 7s"
        assert app.run_state["current_step"] is None
        content = log_path.read_text(encoding="utf-8")
        assert "ERROR: Command timed out after 7s" in content
    finally:
        shutil.rmtree(run_root, ignore_errors=True)
