#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: spark/spark_multi_tenant
# Strategy: shell_wrapper_variant
# Imported reference: spark/spark_multi_tenant_job_execution
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_spark_multi_tenant.sh

static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_spark_multi_tenant.sh"
