#!/usr/bin/env sh
set -eu

if [ -f "/workspace/env.sh" ]; then
  # shellcheck disable=SC1091
  . /workspace/env.sh
fi

if [ -n "${NPM_CONFIG_PREFIX:-}" ]; then
  export PATH="$PATH:$NPM_CONFIG_PREFIX/bin"
fi

PROMPT_PATH=/workspace/PROMPT.md
RUNBOOK_DIR=/workspace/runbooks

if [ ! -f "$PROMPT_PATH" ]; then
  echo "PROMPT.md not found in /workspace" >&2
  exit 1
fi

if [ -n "${BENCHMARK_AGENT_LOG:-}" ]; then
  mkdir -p "$(dirname "$BENCHMARK_AGENT_LOG")"
  exec >>"$BENCHMARK_AGENT_LOG" 2>&1
fi

if [ -n "${BENCHMARK_SUBMIT_URL:-}" ]; then
  echo "Submission URL: $BENCHMARK_SUBMIT_URL"
fi

if [ "${BENCHMARK_ENTRYPOINT_NO_EXEC:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

exec "$@"
