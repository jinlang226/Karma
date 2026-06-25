#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: nginx-ingress/create_ingress
# Strategy: shell_wrapper_variant
# Imported reference: nginx-ingress/ingress_route_ready
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_nginx_ingress_route.sh

static_solver_export_nginx_defaults

static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_nginx_ingress_route.sh"
