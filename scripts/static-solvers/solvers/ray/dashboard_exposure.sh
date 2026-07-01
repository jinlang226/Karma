#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: ray/dashboard_exposure
# Strategy: shell_wrapper_variant
# Imported reference: ray/dashboard_reachable
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_ray_dashboard.sh

static_solver_export_namespace_if_unset "ray"

static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_ray_dashboard.sh"
