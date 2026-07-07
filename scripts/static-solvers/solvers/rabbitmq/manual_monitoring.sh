#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: rabbitmq/manual_monitoring
# Strategy: python_wrapper
# Imported reference: rabbitmq-experiments/manual_monitoring
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_rabbit_monitoring.sh
# Notes: Wrapper around in-tree Python solver.

static_solver_run_vendored_resource_python "rabbitmq-experiments/manual_monitoring" "submitted static solver for rabbitmq/manual_monitoring"
