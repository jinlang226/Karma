#!/usr/bin/env bash
# Codex agent entrypoint (docker sandbox mode).
#
# KARMA mounts the stage dir at /workspace: the task prompt is ./prompt.txt and
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

# Headless, non-interactive Codex run (mirrors the old repo's codex profile).
codex --dangerously-bypass-approvals-and-sandbox exec ${MODEL_ARG} \
  -C /workspace --skip-git-repo-check \
  "$(cat "$PROMPT_FILE")" > "$TMP_FILE"

mv -f "$TMP_FILE" "$SUBMIT_FILE"
