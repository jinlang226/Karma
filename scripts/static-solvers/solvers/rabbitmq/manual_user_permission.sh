#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: rabbitmq/manual_user_permission
# Strategy: native_python
# Notes: Repairs least-privilege grants and reconciles inherited app-queue
# declaration drift only when the live app-client logs prove that immutable
# queue arguments from an earlier stage block readiness.

python3 "${SCRIPT_DIR}/manual_user_permission.py"
static_solver_write_submit "submitted static solver for rabbitmq/manual_user_permission"
