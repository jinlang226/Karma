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

# Headless, non-interactive Codex run, against the current working directory so
# it works in both sandbox modes (-C "$PWD" rather than a hardcoded /workspace).
# tee so the answer lands in BOTH submit.txt and stdout (captured to agent.log).
codex --dangerously-bypass-approvals-and-sandbox exec ${MODEL_ARG} \
  -C "$PWD" --skip-git-repo-check \
  "$(cat "$PROMPT_FILE")" 2>&1 | tee "$TMP_FILE"

mv -f "$TMP_FILE" "$SUBMIT_FILE"
