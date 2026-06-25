#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: elasticsearch/transform-job-recovery
# Strategy: direct_shell
# Imported reference: elasticsearch/transform-job-recovery
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_es_transform.sh

static_solver_export_namespace_if_unset "elasticsearch"
static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_es_transform.sh"
