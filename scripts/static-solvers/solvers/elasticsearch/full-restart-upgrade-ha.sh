#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/full-restart-upgrade-ha
# Strategy: direct_shell
# Imported reference: elasticsearch/full-restart-upgrade-ha
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_es_upgrade.sh

static_solver_export_namespace_if_unset "elasticsearch"
static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_es_upgrade.sh"
