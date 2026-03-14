import json
import tempfile
from pathlib import Path

from app.orchestrator_core import namespace_runtime


def test_namespace_env_vars_exports_default_and_roles():
    ctx = {
        "roles": {
            "default": "cmp-wf-default",
            "cluster-a": "cmp-wf-a",
        },
        "default_role": "default",
        "resolved_params": {
            "cluster_prefix": "rabbitmq-a",
            "replicas": 3,
        },
    }

    out = namespace_runtime.namespace_env_vars(ctx)

    assert out["BENCH_NAMESPACE"] == "cmp-wf-default"
    assert out["BENCH_NS_DEFAULT"] == "cmp-wf-default"
    assert out["BENCH_NS_CLUSTER_A"] == "cmp-wf-a"
    assert out["BENCH_PARAM_CLUSTER_PREFIX"] == "rabbitmq-a"
    assert out["BENCH_PARAM_REPLICAS"] == "3"
    assert json.loads(out["BENCH_NAMESPACE_MAP"]) == {
        "cluster-a": "cmp-wf-a",
        "default": "cmp-wf-default",
    }


def test_prepare_exec_command_renders_placeholders_and_kubectl_namespace():
    ctx = {
        "roles": {
            "default": "cmp-wf-default",
            "target": "cmp-wf-target",
        },
        "default_role": "default",
    }
    item = {
        "namespace_role": "target",
        "command": ["kubectl", "get", "pods", "-n", "${NS_target}"],
    }

    command, env = namespace_runtime.prepare_exec_command(item, ctx, root=Path.cwd(), environ={})

    assert command == ["kubectl", "get", "pods", "-n", "cmp-wf-target"]
    assert env["BENCH_NAMESPACE"] == "cmp-wf-default"
    assert env["BENCH_NS_TARGET"] == "cmp-wf-target"


def test_render_manifest_paths_materializes_rendered_copy():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        manifest = tmp_path / "input.yaml"
        manifest.write_text(
            """
apiVersion: v1
kind: ConfigMap
metadata:
  name: test
  namespace: ${BENCH_NAMESPACE}
data:
  prefix: ${BENCH_PARAM_CLUSTER_PREFIX}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        render_dir = tmp_path / "rendered"
        ctx = {
            "roles": {
                "default": "cmp-wf-default",
            },
            "resolved_params": {"cluster_prefix": "rabbitmq-a"},
        }

        command = ["kubectl", "apply", "-f", str(manifest.relative_to(tmp_path))]
        rendered_cmd = namespace_runtime.render_manifest_paths(
            command,
            ctx,
            render_dir=render_dir,
            root=tmp_path,
        )

        assert rendered_cmd != command
        rendered_path = Path(rendered_cmd[3])
        assert rendered_path.is_file()
        assert rendered_path.parent == render_dir
        text = rendered_path.read_text(encoding="utf-8")
        assert "namespace: cmp-wf-default" in text
        assert "prefix: rabbitmq-a" in text


def test_attach_workflow_namespace_context_sets_stage_context_and_prompt():
    rows = [
        {
            "stage": {
                "id": "stage-1",
                "service": "rabbitmq-experiments",
                "case": "manual_monitoring",
            },
            "case_data": {
                "detailedInstructions": "do something",
                "operatorContext": "ops",
            },
            "resolved_params": {},
            "param_warnings": [],
            "namespace_contract": {"default_role": "target"},
        }
    ]
    workflow = {"spec": {"namespaces": ["cluster_a", "cluster_b"]}}

    def _alias_map(aliases, run_token, prefix):
        assert aliases == ["cluster_a", "cluster_b"]
        assert run_token == "abc"
        assert prefix == "wf"
        return {
            "cluster_a": "cmp-wf-a",
            "cluster_b": "cmp-wf-b",
        }

    def _stage_ctx(_stage, _alias):
        return {
            "roles": {
                "default": "cmp-wf-a",
                "target": "cmp-wf-b",
            }
        }

    def _prompt_block(_meta, resolved_params, param_warnings, namespace_context):
        assert resolved_params == {}
        assert param_warnings == []
        return f"ns={namespace_context.get('roles', {}).get('target')}"

    alias_map = namespace_runtime.attach_workflow_namespace_context(
        rows,
        workflow,
        "abc",
        "wf",
        build_alias_namespace_map_fn=_alias_map,
        resolve_stage_namespace_context_fn=_stage_ctx,
        render_case_prompt_block_fn=_prompt_block,
    )

    assert alias_map == {
        "cluster_a": "cmp-wf-a",
        "cluster_b": "cmp-wf-b",
    }
    assert rows[0]["namespace_context"]["default_role"] == "target"
    assert rows[0]["prompt_block"] == "ns=cmp-wf-b"
