#!/usr/bin/env bash
# Codex agent entrypoint (works in both local and docker sandbox modes).
#
# KARMA runs this with the working directory set to the stage dir (docker mounts
# it at /workspace; local runs it in place). The task prompt is ./prompt.txt and
# completion is signalled by creating ./submit.txt (KARMA polls for its
# existence). We render Codex's answer to a temp file and atomically rename it
# to submit.txt only after Codex exits -- never create submit.txt early, or the
# agent is killed before it acts.
#
# The agent inherits KUBECONFIG (KARMA's kubectl proxy), BENCH_NAMESPACE /
# BENCH_NS_* and BENCH_PARAM_*, so `kubectl` run via Codex talks to the cluster
# through the proxy automatically. Auth comes from a mounted ~/.codex/auth.json
# or OPENAI_API_KEY in the environment.
set -uo pipefail

PROMPT_FILE="prompt.txt"
SUBMIT_FILE="submit.txt"
TMP_FILE=".submit.partial"

MODEL_ARG=""
[ -n "${CODEX_MODEL:-}" ] && MODEL_ARG="-m ${CODEX_MODEL}"

PROMPT="$(cat "$PROMPT_FILE")"

# Persistent-session mode (workflow agent_session: persistent): keep ONE Codex
# conversation across stages. Point CODEX_HOME at a per-run dir so "resume the
# most recent session" (--last) can only pick THIS run's session, seeding it
# with the host auth/config so the CLI stays authenticated. Stage 0 starts the
# session; later stages resume it.
RESUME=""
if [ -n "${BENCH_SESSION_PERSIST:-}" ] && [ -n "${BENCH_SESSION_DIR:-}" ]; then
  export CODEX_HOME="${BENCH_SESSION_DIR}/codex"
  mkdir -p "$CODEX_HOME"
  for f in auth.json config.toml; do
    [ -f "$CODEX_HOME/$f" ] || cp -f "$HOME/.codex/$f" "$CODEX_HOME/$f" 2>/dev/null || true
  done
  [ "${BENCH_SESSION_STAGE_INDEX:-0}" != "0" ] && RESUME="1"
fi

# Headless, non-interactive Codex run, against the current working directory so
# it works in both sandbox modes (-C "$PWD" rather than a hardcoded /workspace).
# tee so the answer lands in BOTH submit.txt and stdout (captured to agent.log).
if [ -n "$RESUME" ]; then
  codex --dangerously-bypass-approvals-and-sandbox exec resume --last ${MODEL_ARG} \
    "$PROMPT" 2>&1 | tee "$TMP_FILE"
else
  codex --dangerously-bypass-approvals-and-sandbox exec ${MODEL_ARG} \
    -C "$PWD" --skip-git-repo-check \
    "$PROMPT" 2>&1 | tee "$TMP_FILE"
fi

mv -f "$TMP_FILE" "$SUBMIT_FILE"
