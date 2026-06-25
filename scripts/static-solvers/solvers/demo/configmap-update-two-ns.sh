#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: demo/configmap-update-two-ns
# Strategy: direct_shell
# Imported reference: demo/configmap-update-two-ns
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_demo_configmap_update_two_ns.sh

static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_demo_configmap_update_two_ns.sh"
