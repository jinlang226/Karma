import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import List, Optional, Tuple

from openai import OpenAI


MAX_OBS_CHARS = 8000


def _log(message: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"[{ts}] {message}", flush=True)


def _read_prompt() -> str:
    prompt_path = Path("/workspace/PROMPT.md")
    if not prompt_path.exists():
        raise FileNotFoundError("/workspace/PROMPT.md not found")
    parts = [prompt_path.read_text()]

    runbook_dir = Path("/workspace/runbooks")
    if runbook_dir.is_dir():
        for runbook in sorted(runbook_dir.iterdir()):
            if runbook.is_file():
                parts.append("\n---\n")
                parts.append(f"Runbook: {runbook.name}\n")
                parts.append(runbook.read_text())

    submit_file = os.environ.get("BENCHMARK_SUBMIT_FILE", "")
    parts.append(
        "\n---\nWhen the task is complete, run: touch {submit_file}\n".format(
            submit_file=submit_file
        )
    )
    parts.append("Then reply with TERMINATE.\n")
    return "\n".join(parts)


def _read_workflow_state_summary() -> Optional[str]:
    state_path = Path("/workspace/WORKFLOW_STATE.json")
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text())
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    active = payload.get("active_stage_id")
    idx = payload.get("active_stage_index")
    total = payload.get("stage_total")
    solve = payload.get("solve_status")
    return f"workflow_state: active={active} index={idx}/{total} solve_status={solve}"


def _run_shell(command: str) -> str:
    _log(f"run_shell: {command}")
    result = subprocess.run(
        command,
        shell=True,
        cwd="/workspace",
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output = result.stdout.strip()
    error = result.stderr.strip()
    combined = "\n".join(part for part in [output, error] if part)
    if not combined:
        combined = f"(exit {result.returncode})"
    _log(f"run_shell output (truncated to {MAX_OBS_CHARS} chars)")
    return combined[:MAX_OBS_CHARS]


def _extract_field(lines: List[str], field: str) -> Optional[str]:
    field_lower = field.lower()
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        if line.lower().startswith(field_lower):
            value = line.split(":", 1)[-1].strip()
            if value:
                return value
            for next_line in lines[idx + 1 :]:
                if next_line.strip():
                    return next_line.strip()
            return ""
    return None


def _parse_action(text: str) -> Tuple[Optional[str], Optional[str]]:
    lines = [line.rstrip() for line in text.splitlines()]
    action = _extract_field(lines, "Action")
    action_input = _extract_field(lines, "Action Input")
    if action:
        action = action.strip()
    if action_input is not None:
        action_input = action_input.strip()
    return action, action_input


def _submit() -> None:
    _log("submit: signaling completion")
    submit_url = os.environ.get("BENCHMARK_SUBMIT_URL")
    submit_file = os.environ.get("BENCHMARK_SUBMIT_FILE")
    if submit_url:
        try:
            subprocess.run(
                ["curl", "-sS", "-X", "POST", submit_url],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass
    if submit_file:
        Path(submit_file).touch()


def _wait_for_submit_result(last_attempt: int, timeout: Optional[int] = None) -> Optional[dict]:
    result_path = os.environ.get("BENCHMARK_SUBMIT_RESULT_FILE")
    if not result_path:
        return None
    if timeout is None:
        timeout = int(os.environ.get("BENCHMARK_SUBMIT_RESULT_TIMEOUT", "1200"))
    _log(f"submit_result: waiting (last_attempt={last_attempt})")
    path = Path(result_path)
    start = time.time()
    while time.time() - start < timeout:
        if not path.exists():
            time.sleep(1.0)
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            time.sleep(1.0)
            continue
        attempt = payload.get("attempt")
        if attempt is None or attempt <= last_attempt:
            time.sleep(1.0)
            continue
        _log(f"submit_result: received attempt={attempt} status={payload.get('status')}")
        return payload
    _log("submit_result: timeout")
    return None


def _submission_file_exists() -> bool:
    submit_file = os.environ.get("BENCHMARK_SUBMIT_FILE")
    if not submit_file:
        return False
    return Path(submit_file).exists()


def _build_system_prompt() -> str:
    return (
        "You are a Kubernetes SRE agent. Use the format:\n"
        "Thought: <brief>\n"
        "Action: run_shell\n"
        "Action Input: <single line command>\n"
        "\n"
        "Or when done:\n"
        "Thought: <brief>\n"
        "Action: submit\n"
        "Action Input: done\n"
        "\n"
        "Rules:\n"
        "- Only one Action per response.\n"
        "- Action Input must be a single line (no code fences).\n"
        "- Use run_shell to execute commands.\n"
        "- When finished, submit.\n"
        "- If submit_result has workflow.continue=true, re-read PROMPT.md and continue.\n"
        "- If submit_result has workflow.final=true, stop.\n"
    )


def _run_agent(prompt: str) -> None:
    model = os.environ.get("LLM_MODEL")
    api_key = os.environ.get("LLM_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL", "")
    temperature = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
    max_steps = int(os.environ.get("REACT_MAX_STEPS", "50"))
    step_delay = float(os.environ.get("REACT_STEP_DELAY_SEC", "0"))

    if not model:
        raise RuntimeError("LLM_MODEL is required")
    if not api_key:
        raise RuntimeError("LLM_API_KEY is required")

    if api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = api_key
    if base_url and not os.environ.get("OPENAI_BASE_URL"):
        os.environ["OPENAI_BASE_URL"] = base_url

    client = OpenAI(api_key=api_key, base_url=base_url or None)

    messages = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": prompt},
    ]

    last_attempt = 0
    for _ in range(max_steps):
        _log("llm: requesting completion")
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        content = response.choices[0].message.content or ""
        _log(f"llm: response\n{content}")
        messages.append({"role": "assistant", "content": content})

        action, action_input = _parse_action(content)
        _log(f"parsed: action={action} action_input={action_input}")
        if not action:
            messages.append(
                {
                    "role": "user",
                    "content": "Observation: ERROR: missing Action. Follow the format.",
                }
            )
            continue

        action_lower = action.lower()
        if action_lower == "submit":
            _submit()
            result = _wait_for_submit_result(last_attempt)
            if not result:
                return
            last_attempt = result.get("attempt", last_attempt)
            workflow = result.get("workflow") if isinstance(result, dict) else None
            if isinstance(workflow, dict) and workflow.get("continue"):
                _log("submit_result: workflow continue")
                next_prompt = _read_prompt()
                observation = [
                    "Observation: workflow advanced to next stage.",
                    f"next_stage_id={workflow.get('next_stage_id')}",
                    f"reason={workflow.get('reason')}",
                    "",
                    "Updated Prompt:",
                    next_prompt[:MAX_OBS_CHARS],
                ]
                state_summary = _read_workflow_state_summary()
                if state_summary:
                    observation.extend(["", state_summary])
                messages.append({"role": "user", "content": "\n".join(observation)})
                continue
            if isinstance(workflow, dict) and workflow.get("final"):
                _log("submit_result: workflow final")
                return
            if result.get("status") == "failed" and result.get("can_retry"):
                _log("submit_result: failed, retrying")
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Observation: verification failed. "
                            f"error={result.get('last_error') or 'unknown'}; "
                            f"attempts_left={result.get('attempts_left')}; "
                            f"time_left_sec={result.get('time_left_sec')}"
                        ),
                    }
                )
                continue
            return
        if action_lower != "run_shell":
            messages.append(
                {
                    "role": "user",
                    "content": f"Observation: ERROR: unknown Action {action}.",
                }
            )
            continue
        if not action_input:
            messages.append(
                {
                    "role": "user",
                    "content": "Observation: ERROR: missing Action Input.",
                }
            )
            continue

        observation = _run_shell(action_input)
        if _submission_file_exists():
            _submit()
            result = _wait_for_submit_result(last_attempt)
            if not result:
                return
            last_attempt = result.get("attempt", last_attempt)
            workflow = result.get("workflow") if isinstance(result, dict) else None
            if isinstance(workflow, dict) and workflow.get("continue"):
                _log("submit_result: workflow continue after file submit")
                next_prompt = _read_prompt()
                observation_lines = [
                    "Observation: workflow advanced to next stage.",
                    f"next_stage_id={workflow.get('next_stage_id')}",
                    f"reason={workflow.get('reason')}",
                    "",
                    "Updated Prompt:",
                    next_prompt[:MAX_OBS_CHARS],
                ]
                state_summary = _read_workflow_state_summary()
                if state_summary:
                    observation_lines.extend(["", state_summary])
                messages.append({"role": "user", "content": "\n".join(observation_lines)})
                continue
            if isinstance(workflow, dict) and workflow.get("final"):
                _log("submit_result: workflow final")
                return
            if result.get("status") == "failed" and result.get("can_retry"):
                _log("submit_result: failed after file submit, retrying")
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Observation: verification failed. "
                            f"error={result.get('last_error') or 'unknown'}; "
                            f"attempts_left={result.get('attempts_left')}; "
                            f"time_left_sec={result.get('time_left_sec')}"
                        ),
                    }
                )
                continue
            return
        messages.append({"role": "user", "content": f"Observation: {observation}"})
        if step_delay > 0:
            time.sleep(step_delay)

    raise RuntimeError("Reached max steps without submitting.")


def main() -> None:
    prompt = _read_prompt()
    _run_agent(prompt)


if __name__ == "__main__":
    main()
