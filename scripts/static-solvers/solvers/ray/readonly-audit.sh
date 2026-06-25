#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: ray/readonly-audit
# Strategy: submit_only_candidate
# Notes: Static no-op submit candidate; requires runtime validation.

static_solver_submit_only "submitted static solver for ray/readonly-audit"
