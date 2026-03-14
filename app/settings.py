import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
RESOURCES_DIR = ROOT / "resources"
STATIC_DIR = ROOT / "static"


def resolve_runs_dir(root=None, runs_subdir=None):
    root_path = (Path(root) if root is not None else ROOT).resolve()
    configured = runs_subdir
    if configured is None:
        configured = os.environ.get("BENCHMARK_RUNS_SUBDIR", "runs")
    configured = (configured or "runs").strip() or "runs"

    candidate = Path(configured)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (root_path / candidate).resolve()

    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise RuntimeError(
            "BENCHMARK_RUNS_SUBDIR must resolve inside the repository root"
        ) from exc
    return resolved


RUNS_DIR = resolve_runs_dir()
ACTION_TRACE_LOG = Path(
    os.environ.get("BENCHMARK_ACTION_TRACE_LOG", str(RUNS_DIR / "proxy-trace.jsonl"))
)
PROXY_CONTROL_URL = os.environ.get("BENCHMARK_PROXY_CONTROL_URL")
PROXY_CONTROL_TIMEOUT = float(os.environ.get("BENCHMARK_PROXY_CONTROL_TIMEOUT", "2.0"))

SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8080

MAX_ATTEMPTS = 20
MAX_TIME_MINUTES = 20

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
