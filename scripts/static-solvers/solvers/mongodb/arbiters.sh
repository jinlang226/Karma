#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/arbiters
# Strategy: direct_shell
# Imported reference: mongodb/arbiters
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_mongo_arbiters.sh

static_solver_export_namespace_if_unset "mongodb"
# Notes: Corrected validation solver: sets default read/write concern before adding arbiter.

static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_mongo_arbiters.sh"
