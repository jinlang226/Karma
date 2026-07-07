#!/usr/bin/env bash
# GitHub Copilot CLI agent entrypoint (local + docker sandbox modes).
#
# KARMA runs this with the working directory set to the stage dir (docker mounts
# it at /workspace; local runs it in place). The task prompt is ./prompt.txt and
# completion is signalled by creating ./submit.txt (KARMA polls for its
# existence -- karma/runtime/case.py:_wait_for_submit). We render Copilot's
# output to a temp file and atomically rename it to submit.txt only after
# Copilot exits -- never create submit.txt early, or the agent is killed before
# it acts.
#
# The agent inherits KUBECONFIG (KARMA's kubectl proxy), BENCH_NAMESPACE /
# BENCH_NS_* and BENCH_PARAM_*, so `kubectl` run via Copilot's shell tool talks
# to the cluster through the proxy automatically. Auth comes from a host
# `copilot`/`gh` login (local) or GITHUB_TOKEN in the environment (docker).
set -uo pipefail

PROMPT_FILE="prompt.txt"
SUBMIT_FILE="submit.txt"
TMP_FILE=".submit.partial"

MODEL_ARG=""
[ -n "${KARMA_COPILOT_AGENT_MODEL:-}" ] && MODEL_ARG="--model ${KARMA_COPILOT_AGENT_MODEL}"

PROMPT="$(cat "$PROMPT_FILE")"
# Optional workflow-level system prompt (spec.system_prompt): Copilot has no
# system-prompt flag, so prepend it to the task prompt.
if [ -f "system_prompt.txt" ]; then
  PROMPT="$(cat system_prompt.txt)

$PROMPT"
fi

# Persistent-session mode (workflow agent_session: persistent): keep ONE Copilot
# conversation across stages by reusing a stable --session-id. Copilot creates
# the session on first use and resumes it on subsequent stages with the same id.
SESSION_ARG=""
if [ -n "${BENCH_SESSION_PERSIST:-}" ] && [ -n "${BENCH_SESSION_ID:-}" ]; then
  SESSION_ARG="--session-id ${BENCH_SESSION_ID}"
fi

# Headless, full-auto Copilot run: --prompt for the non-interactive task and
# --allow-all so every tool (shell/kubectl) runs without an approval prompt (the
# Copilot analogue of claude_code's --dangerously-skip-permissions). tee the
# output to BOTH the temp submit file and stdout (the sandbox captures stdout to
# agent.log, so the full turn-by-turn is recorded even on timeout).
copilot --prompt "$PROMPT" \
  --allow-all ${MODEL_ARG} ${SESSION_ARG} \
  2>&1 | tee "$TMP_FILE"

mv -f "$TMP_FILE" "$SUBMIT_FILE"
