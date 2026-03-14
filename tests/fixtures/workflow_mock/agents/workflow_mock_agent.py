#!/usr/bin/env python3
import json
import os
import re
import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SUBMIT_FILE = Path(os.environ.get("BENCHMARK_SUBMIT_FILE", "/workspace/submit.signal"))
WORKSPACE_ROOT = SUBMIT_FILE.resolve().parent if SUBMIT_FILE.is_absolute() else Path("/workspace")
PROMPT_PATH = WORKSPACE_ROOT / "PROMPT.md"
SUBMIT_RESULT = Path(
    os.environ.get("BENCHMARK_SUBMIT_RESULT_FILE", str(WORKSPACE_ROOT / "submit_result.json"))
)

SOLVERS = {
    "stage_seed": [
        "python3",
        str(
            REPO_ROOT
            / "tests/fixtures/workflow_mock/resources/workflow-mock/stage_seed/solver/solve.py"
        ),
    ],
    "stage_scale": [
        "python3",
        str(
            REPO_ROOT
            / "tests/fixtures/workflow_mock/resources/workflow-mock/stage_scale/solver/solve.py"
        ),
    ],
    "stage_finalize": [
        "python3",
        str(
            REPO_ROOT
            / "tests/fixtures/workflow_mock/resources/workflow-mock/stage_finalize/solver/solve.py"
        ),
    ],
}


def active_stage_context() -> tuple[str, dict]:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    match = re.search(r"Active Stage:\s*\d+/\d+\s*\(([^)]+)\)", text)
    if not match:
        raise RuntimeError("cannot parse active stage from PROMPT.md")
    stage_id = match.group(1).strip()

    params = {}
    if "Resolved Params" in text:
        block_match = re.search(r"Resolved Params\s*\n((?:- .*\n)+)", text)
        if block_match:
            for line in block_match.group(1).splitlines():
                line = line.strip()
                if not line.startswith("- "):
                    continue
                body = line[2:]
                if ":" not in body:
                    continue
                key, value = body.split(":", 1)
                params[key.strip()] = value.strip().strip('"')
    return stage_id, params


def wait_for_submit_result(last_marker):
    deadline = time.time() + 1200
    while time.time() < deadline:
        if not SUBMIT_RESULT.exists():
            time.sleep(0.5)
            continue
        try:
            payload = json.loads(SUBMIT_RESULT.read_text(encoding="utf-8"))
        except Exception:
            time.sleep(0.5)
            continue
        marker = (
            payload.get("attempt"),
            ((payload.get("workflow") or {}).get("stage_id")),
            ((payload.get("workflow") or {}).get("continue")),
            ((payload.get("workflow") or {}).get("final")),
        )
        if marker == last_marker:
            time.sleep(0.5)
            continue
        return payload, marker
    raise TimeoutError("timed out waiting for submit_result")


def run_solver(stage_id: str, params: dict) -> None:
    cmd = SOLVERS.get(stage_id)
    if not cmd:
        raise RuntimeError(f"no solver mapped for stage: {stage_id}")
    extra = []
    if stage_id == "stage_seed":
        phase = params.get("expected_phase")
        if phase:
            extra.extend(["--phase", phase])
    elif stage_id == "stage_scale":
        phase = params.get("expected_phase")
        replicas = params.get("expected_replicas")
        if phase:
            extra.extend(["--phase", phase])
        if replicas:
            extra.extend(["--replicas", replicas])
    elif stage_id == "stage_finalize":
        phase = params.get("expected_phase")
        replicas = params.get("expected_replicas")
        migration = params.get("expected_migration")
        if phase:
            extra.extend(["--phase", phase])
        if replicas:
            extra.extend(["--replicas", replicas])
        if migration:
            extra.extend(["--migration", migration])
    full_cmd = cmd + extra
    print(f"[workflow-mock-agent] solve {stage_id}: {' '.join(full_cmd)}", flush=True)
    subprocess.check_call(full_cmd, cwd=str(REPO_ROOT), env=os.environ.copy())


def main() -> int:
    marker = None
    while True:
        stage_id, params = active_stage_context()
        run_solver(stage_id, params)
        SUBMIT_FILE.touch()
        payload, marker = wait_for_submit_result(marker)

        if payload.get("can_retry"):
            continue

        workflow = payload.get("workflow") or {}
        if workflow.get("continue"):
            continue
        if workflow.get("final"):
            print("[workflow-mock-agent] workflow final", flush=True)
            return 0

        # Non-workflow fallback.
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
