#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: nginx-ingress/otel_log_format
# Strategy: shell_wrapper_variant
# Imported reference: nginx-ingress/otel_ingress_logging_ready
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_nginx_otel.sh

static_solver_export_nginx_defaults "demo" "ingress-nginx" "otel"

static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_nginx_otel.sh"
