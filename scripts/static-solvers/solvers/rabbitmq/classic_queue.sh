#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: rabbitmq/classic_queue
# Strategy: python_wrapper
# Imported reference: rabbitmq-experiments/classic_queue
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_rabbit_classic.sh
# Notes: Wrapper around in-tree Python solver.

static_solver_run_vendored_resource_python "rabbitmq-experiments/classic_queue" "submitted static solver for rabbitmq/classic_queue"
