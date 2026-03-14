from pathlib import Path
from types import SimpleNamespace

from app.orchestrator_core.agent_runtime import resolve_agent_defaults
from app.settings import ROOT


def _args(**overrides):
    payload = {
        "agent": "react",
        "agent_tag": None,
        "agent_build": True,
        "sandbox": "docker",
        "docker_image": None,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _agent_registry():
    return {
        "react": {
            "dockerfile": "agent_tests/react/Dockerfile",
            "tag": "bench-agent-react:latest",
        }
    }


def test_agent_build_uses_registry_tag_and_sets_docker_image():
    repo_root = Path(ROOT)
    args = _args()
    calls = []

    def _fake_build(tag, dockerfile, _ctx):
        calls.append((tag, str(dockerfile)))

    built = resolve_agent_defaults(
        args,
        repo_root,
        agent_registry=_agent_registry(),
        docker_build_image=_fake_build,
    )

    assert built is not None
    assert args.docker_image == built
    assert len(calls) == 1
    assert calls[0][0] == built
    assert calls[0][1].endswith("Dockerfile")


def test_agent_build_rejects_explicit_docker_image_mix():
    repo_root = Path(ROOT)
    args = _args(docker_image="custom:image")
    failed = False
    try:
        resolve_agent_defaults(
            args,
            repo_root,
            agent_registry=_agent_registry(),
            docker_build_image=lambda *_args, **_kwargs: None,
        )
    except RuntimeError as exc:
        failed = True
        assert "Use --agent-build or --docker-image" in str(exc)
    assert failed is True
