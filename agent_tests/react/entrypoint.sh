#!/usr/bin/env sh
set -eu

. /opt/agent/entrypoint_common.sh

python /app/run_agent.py
