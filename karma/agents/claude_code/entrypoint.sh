#!/usr/bin/env bash
# Claude Code agent entrypoint (local sandbox mode).
#
# KARMA launches this with the working directory set to the stage dir. The task
# prompt is in ./prompt.txt; the agent signals completion by creating
# ./submit.txt. KARMA detects completion by submit.txt EXISTENCE (see
# karma/runtime/case.py:_wait_for_submit), so we extract claude's answer into a
# temp file and atomically rename it to submit.txt only after claude exits --
# never create submit.txt early, or the agent is killed before it acts.
#
# We run claude with the full structured event stream (--output-format
# stream-json --verbose) so agent.log records the agent's entire turn-by-turn:
# every reasoning text block plus every tool_use (bash/kubectl) and tool_result.
# The sandbox captures this process's stdout+stderr into agent.log (both local
# and docker modes; see karma/sandbox.py), so we echo the stream to stdout and
# then parse the final `result` event for the submission.
#
# The agent inherits KUBECONFIG (pointing at KARMA's kubectl proxy),
# BENCH_NAMESPACE / BENCH_NS_* / KARMA_NS_*, KARMA_KUBECTL_PROXY_PORT, and any
# BENCH_PARAM_* from the environment, so kubectl run via claude's Bash tool
# talks to the cluster through the proxy automatically.
set -uo pipefail

# In the docker sandbox the container runs as root, and Claude Code refuses
# --dangerously-skip-permissions as root unless it knows it is sandboxed. This
# env tells it so (harmless in local mode, where we run as a normal user).
export IS_SANDBOX=1

PROMPT_FILE="prompt.txt"
SUBMIT_FILE="submit.txt"
TMP_FILE=".submit.partial"
STREAM_FILE=".agent.stream.jsonl"
MODEL="${KARMA_CLAUDE_AGENT_MODEL:-sonnet}"
# Optional reasoning effort (low|medium|high|xhigh|max). Empty -> omit the flag
# so the CLI keeps its own default; set KARMA_CLAUDE_AGENT_EFFORT to pair an
# effort level with the model (e.g. MODEL=opus + EFFORT=low).
EFFORT="${KARMA_CLAUDE_AGENT_EFFORT:-}"
EFFORT_ARGS=()
if [ -n "$EFFORT" ]; then
  EFFORT_ARGS=(--effort "$EFFORT")
fi

# Stream the full event log to stdout (-> agent.log) in REAL TIME via tee, and
# also to a temp file for parsing. Real-time matters: when KARMA times out and
# kills the agent mid-run, agent.log still holds the partial turn-by-turn (the
# runs you most want to debug), instead of being lost.
claude --print --verbose --output-format stream-json \
  --model "$MODEL" \
  "${EFFORT_ARGS[@]}" \
  --dangerously-skip-permissions \
  "$(cat "$PROMPT_FILE")" \
  2>&1 | tee "$STREAM_FILE"

# Extract the agent's final answer for submit.txt: the last `result` event's
# text, falling back to the last assistant text block if claude emitted no
# result event (e.g. an early error).
node -e '
const fs = require("fs");
let out = "", lastAssistant = "";
try {
  for (const line of fs.readFileSync(process.argv[1], "utf8").split("\n")) {
    if (!line.trim()) continue;
    let o; try { o = JSON.parse(line); } catch (e) { continue; }
    if (o.type === "result" && typeof o.result === "string") out = o.result;
    if (o.type === "assistant" && o.message && Array.isArray(o.message.content)) {
      const t = o.message.content.filter(c => c.type === "text").map(c => c.text).join("");
      if (t) lastAssistant = t;
    }
  }
} catch (e) {}
fs.writeFileSync(process.argv[2], out || lastAssistant);
' "$STREAM_FILE" "$TMP_FILE"

mv -f "$TMP_FILE" "$SUBMIT_FILE"
rm -f "$STREAM_FILE"
