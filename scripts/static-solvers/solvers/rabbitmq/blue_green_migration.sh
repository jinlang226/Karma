#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: rabbitmq/blue_green_migration
# Strategy: python_wrapper
# Imported reference: rabbitmq-experiments/blue_green_migration
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_rabbit_blue_green.sh
# Notes: Wrapper around in-tree Python solver.

static_solver_run_vendored_resource_python "rabbitmq-experiments/blue_green_migration" "submitted static solver for rabbitmq/blue_green_migration"
