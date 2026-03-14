#!/usr/bin/env sh
set -eu

BENCHMARK_ENTRYPOINT_NO_EXEC=1 . /opt/agent/entrypoint_common.sh

set +e
"$@"
AGENT_RC=$?
set -e

if [ -n "${BENCHMARK_USAGE_OUTPUT:-}" ]; then
  python3 /opt/agent/collect_token_usage.py \
    --out "$BENCHMARK_USAGE_OUTPUT" \
    --codex-home "${CODEX_HOME:-$HOME/.codex}" \
    --agent-log "${BENCHMARK_AGENT_LOG:-}" \
    || true
fi

exit "$AGENT_RC"
