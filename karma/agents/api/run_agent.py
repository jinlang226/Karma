#!/usr/bin/env python3
"""
KARMA "api" agent -- a self-contained agentic loop over an OpenAI-compatible
chat-completions API (DeepSeek by default).

KARMA runs this with the working directory at the stage dir: the task prompt is
./prompt.txt and completion is signalled by creating ./submit.txt (KARMA polls
for its existence -- karma/runtime/case.py:_wait_for_submit). The agent solves
the task by driving the model in a tool-use loop: it offers a single `bash`
tool, executes each command the model requests against the cluster (the env
already has KUBECONFIG pointing at KARMA's kubectl proxy, plus BENCH_NAMESPACE /
BENCH_* / BENCH_PARAM_*), feeds the output back, and continues until the model
returns a final answer with no further tool call. The full turn-by-turn (each
tool call + its output) is printed to stdout, which the sandbox captures into
agent.log.

No third-party dependencies -- stdlib urllib/json/subprocess only -- so the
local sandbox needs nothing installed and the docker image stays minimal.

Config (environment):
  KARMA_API_BASE_URL  default https://api.deepseek.com
  KARMA_API_KEY       fallback DEEPSEEK_API_KEY, then OPENAI_API_KEY
  KARMA_API_MODEL     default deepseek-v4-flash
  KARMA_API_MAX_STEPS default 40
Because DeepSeek's API is OpenAI-compatible, the same loop targets OpenAI or any
compatible endpoint by changing KARMA_API_BASE_URL / KARMA_API_KEY / KARMA_API_MODEL.
"""
import json
import os
import subprocess
import urllib.error
import urllib.request

BASE_URL = os.environ.get("KARMA_API_BASE_URL", "https://api.deepseek.com").rstrip("/")
API_KEY = (
    os.environ.get("KARMA_API_KEY")
    or os.environ.get("DEEPSEEK_API_KEY")
    or os.environ.get("OPENAI_API_KEY")
    or ""
)
# deepseek-v4-flash (non-thinking) -- the successor to deepseek-chat, which
# DeepSeek deprecates 2026-07-24. Override with KARMA_API_MODEL for any endpoint.
MODEL = os.environ.get("KARMA_API_MODEL", "deepseek-v4-flash")
MAX_STEPS = int(os.environ.get("KARMA_API_MAX_STEPS", "40"))

PROMPT_FILE = "prompt.txt"
SUBMIT_FILE = "submit.txt"
TMP_FILE = ".submit.partial"

SYSTEM = (
    "You are an autonomous SRE agent solving a Kubernetes task. You have one "
    "tool, `bash`, which runs a shell command in an environment already "
    "configured with KUBECONFIG (pointing at the cluster through a proxy) and "
    "the BENCH_NAMESPACE / BENCH_NS_* / BENCH_PARAM_* variables the task refers "
    "to. Use kubectl and other shell tools through `bash` to inspect and change "
    "the cluster. Work step by step, calling `bash` as many times as needed. "
    "When the task is fully complete, reply with a final message and NO tool "
    "call, briefly summarizing what you did -- that message is recorded as your "
    "submission."
)

TOOLS = [{
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command (kubectl, etc.) and return its combined stdout/stderr.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run."},
            },
            "required": ["command"],
        },
    },
}]


def log(msg):
    """Print *msg* to stdout, flushed, so the sandbox captures it into agent.log."""
    print(msg, flush=True)


def call_api(messages):
    """POST *messages* to the chat-completions endpoint and return the parsed reply."""
    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.0,
    }).encode()
    req = urllib.request.Request(
        BASE_URL + "/chat/completions",
        data=body,
        headers={
            "Authorization": "Bearer " + API_KEY,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode())


def run_bash(command):
    """Run *command* in bash and return its combined stdout/stderr (truncated)."""
    try:
        r = subprocess.run(
            ["/bin/bash", "-c", command],
            capture_output=True, text=True, timeout=120,
        )
        out = (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        out = "(command timed out after 120s)"
    except Exception as exc:  # noqa: BLE001 -- report any failure back to the model
        out = f"(failed to run: {exc})"
    return out[:8000] or "(no output)"  # truncate huge outputs


def write_submit(text):
    """Atomically write *text* to submit.txt to signal task completion."""
    with open(TMP_FILE, "w") as fh:
        fh.write(text or "(no answer produced)")
    os.replace(TMP_FILE, SUBMIT_FILE)


def main():
    """Drive the model in a bash tool-use loop until it submits a final answer."""
    try:
        prompt = open(PROMPT_FILE).read()
    except Exception as exc:  # noqa: BLE001
        log(f"FATAL: cannot read {PROMPT_FILE}: {exc}")
        write_submit("(no prompt)")
        return
    # Optional workflow-level system prompt (spec.system_prompt): prepend it so it
    # reaches the model each stage (works for both fresh and resumed sessions).
    try:
        sp = open("system_prompt.txt").read().strip()
        if sp:
            prompt = sp + "\n\n" + prompt
    except OSError:
        pass
    if not API_KEY:
        log("FATAL: no API key (set KARMA_API_KEY or DEEPSEEK_API_KEY)")
        write_submit("(no API key configured)")
        return

    log(f"api agent: model={MODEL} base_url={BASE_URL} max_steps={MAX_STEPS}")
    # Persistent-session mode (workflow agent_session: persistent): keep ONE
    # conversation across stages. Reload the prior transcript and append this
    # stage's prompt instead of re-feeding only the prompt text. The store is a
    # per-run JSON file the runtime points us at via BENCH_SESSION_DIR.
    session_file = None
    if os.environ.get("BENCH_SESSION_PERSIST") and os.environ.get("BENCH_SESSION_DIR"):
        session_file = os.path.join(os.environ["BENCH_SESSION_DIR"], "api-messages.json")
    messages = None
    if session_file and os.path.exists(session_file):
        try:
            with open(session_file) as fh:
                messages = json.load(fh)
            messages.append({"role": "user", "content": prompt})
            log(f"api agent: resumed session ({len(messages)} prior messages)")
        except Exception as exc:  # noqa: BLE001 -- fall back to a fresh thread
            log(f"api agent: could not resume session ({exc}); starting fresh")
            messages = None
    if messages is None:
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ]
    final = ""
    for step in range(1, MAX_STEPS + 1):
        try:
            data = call_api(messages)
        except urllib.error.HTTPError as exc:
            log(f"API HTTPError {exc.code}: {exc.read().decode()[:500]}")
            break
        except Exception as exc:  # noqa: BLE001
            log(f"API error: {exc}")
            break

        # Log the per-call usage as a JSON line so KARMA's
        # evidence.normalize_token_usage captures the api agent's token totals.
        usage = data.get("usage") or {}
        if usage:
            log(json.dumps({"usage": usage}))

        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []
        # Echo back a CLEAN assistant message (role/content/tool_calls only) -- do
        # not return provider-specific fields like reasoning_content, which some
        # APIs (e.g. deepseek-reasoner) reject when sent back as input.
        assistant = {"role": "assistant", "content": msg.get("content")}
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        messages.append(assistant)
        if content:
            log(f"[assistant] {content}")

        if not tool_calls:
            final = content
            break
        for tc in tool_calls:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            cmd = args.get("command", "")
            log(f"[bash step {step}] $ {cmd}")
            out = run_bash(cmd)
            log(out)
            messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": out})
    else:
        log(f"reached max steps ({MAX_STEPS})")
        final = final or "(reached step limit)"

    write_submit(final)
    log("api agent: wrote submit.txt")
    # Persist the full transcript so the next stage resumes this conversation.
    if session_file:
        try:
            os.makedirs(os.path.dirname(session_file), exist_ok=True)
            with open(session_file, "w") as fh:
                json.dump(messages, fh)
            log(f"api agent: saved session ({len(messages)} messages)")
        except Exception as exc:  # noqa: BLE001
            log(f"api agent: could not save session ({exc})")


if __name__ == "__main__":
    main()
