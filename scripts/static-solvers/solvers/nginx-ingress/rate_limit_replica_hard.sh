#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: nginx-ingress/rate_limit_replica_hard
# Strategy: shell_wrapper_variant
# Imported reference: nginx-ingress/rate_limit_ingress
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_nginx_rate_limit.sh
# Notes: Includes sticky service behavior so multiple ingress-nginx pods share the client path for rate limiting.

static_solver_export_nginx_defaults

static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_nginx_rate_limit.sh"
