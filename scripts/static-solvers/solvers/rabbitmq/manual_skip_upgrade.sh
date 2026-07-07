#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: rabbitmq/manual_skip_upgrade
# Strategy: native_python
# Notes: Uses a proxy-resilient in-tree solver and quiesces inherited helper
# deployments before the upgrade so earlier stage scaffolding does not interfere
# with the version hop.

python3 "${SCRIPT_DIR}/manual_skip_upgrade.py"
static_solver_write_submit "submitted static solver for rabbitmq/manual_skip_upgrade"
