import os
from pathlib import Path

from app.settings import ROOT


def discover_agent_registry(repo_root: Path):
    agents_root = repo_root / "agent_tests"
    registry = {}
    if agents_root.exists():
        for dockerfile in sorted(agents_root.glob("*/Dockerfile")):
            name = dockerfile.parent.name
            if name.startswith("_"):
                continue
            registry[name] = {
                "dockerfile": str(dockerfile.relative_to(repo_root)),
                "tag": f"bench-agent-{name}:latest",
            }
    if registry:
        return registry
    # Minimal fallback for environments where discovery fails.
    return {
        "react": {
            "dockerfile": "agent_tests/react/Dockerfile",
            "tag": "bench-agent-react:latest",
        }
    }


DEFAULT_PROXY_LISTEN = os.environ.get("BENCHMARK_PROXY_LISTEN", "127.0.0.1:8081")
DEFAULT_PROXY_CONTROL = os.environ.get("BENCHMARK_PROXY_CONTROL_URL", "http://127.0.0.1:8082")
DEFAULT_PROXY_TIMEOUT = float(os.environ.get("BENCHMARK_PROXY_CONTROL_TIMEOUT", "2.0"))
AGENT_REGISTRY = discover_agent_registry(ROOT)
