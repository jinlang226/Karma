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
# Copilot analogue of claude_code's --dangerously-skip-permissions).
# --output-format json emits JSONL events (one per line); tee the full structured
# stream to stdout (-> agent.log, rich turn-by-turn) and to a temp file to parse.
STREAM_FILE=".agent.stream.jsonl"
# Drop streaming *_delta events (assistant.reasoning_delta / message_delta): each
# is a partial chunk, redundant with the final consolidated assistant.message /
# assistant.reasoning events, and they bloat the log by hundreds of lines. The
# filtered stream is tee'd to stdout (-> agent.log) and to the parse temp file.
copilot --prompt "$PROMPT" \
  --allow-all --output-format json ${MODEL_ARG} ${SESSION_ARG} 2>&1 \
  | grep --line-buffered -vE '"type":"[^"]*_delta"' | tee "$STREAM_FILE"

# Extract the FINAL answer for submit.txt: the last `assistant.message` event's
# data.content (Copilot's JSONL schema), matching claude/codex clean extraction.
# Fall back to the raw stream if nothing parses, so completion is still signalled.
node -e '
const fs = require("fs");
let out = "";
try {
  for (const line of fs.readFileSync(process.argv[1], "utf8").split("\n")) {
    if (!line.trim()) continue;
    let o; try { o = JSON.parse(line); } catch (e) { continue; }
    if (o.type === "assistant.message" && o.data
        && typeof o.data.content === "string" && o.data.content.trim()) {
      out = o.data.content;
    }
  }
} catch (e) {}
fs.writeFileSync(process.argv[2], out);
' "$STREAM_FILE" "$TMP_FILE"
[ -s "$TMP_FILE" ] || cp -f "$STREAM_FILE" "$TMP_FILE"
mv -f "$TMP_FILE" "$SUBMIT_FILE"
rm -f "$STREAM_FILE"
