#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: spark/spark_etl_skew_oom
# Strategy: shell_wrapper_variant
# Imported reference: spark/spark_etl_pipeline_completion
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_spark_etl.sh

static_solver_export_namespace_if_unset "spark-etl"

static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_spark_etl.sh"
