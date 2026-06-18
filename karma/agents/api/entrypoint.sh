#!/usr/bin/env bash
# "api" agent entrypoint (local + docker sandbox modes).
#
# KARMA runs this with the working directory set to the stage dir (docker mounts
# it at /workspace; local runs it in place), so prompt.txt / submit.txt are in
# the CWD. The self-contained agentic loop lives in run_agent.py next to this
# script -- we load it by path while keeping the CWD as the stage dir, so it
# reads ./prompt.txt and writes ./submit.txt there. stdout -> agent.log.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/run_agent.py"
