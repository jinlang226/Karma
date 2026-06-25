#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/decommission
# Strategy: direct_shell
# Imported reference: cockroachdb/decommission
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_crdb_decommission.sh

static_solver_export_namespace_if_unset "cockroachdb"
static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_crdb_decommission.sh"
