#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/external-access-horizons
# Strategy: direct_shell
# Imported reference: mongodb/external-access-horizons
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_mongo_horizons.sh

static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_mongo_horizons.sh"
