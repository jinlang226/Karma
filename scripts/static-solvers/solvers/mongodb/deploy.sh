#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: mongodb/deploy
# Strategy: direct_shell
# Imported reference: mongodb/deploy
# Vendored solver: vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_mongo_deploy.sh

static_solver_export_namespace_if_unset "mongodb"
export BENCH_PARAM_CLUSTER_PREFIX="${BENCH_PARAM_CLUSTER_PREFIX:-mongodb-replica}"
export BENCH_PARAM_HEADLESS_SERVICE_NAME="${BENCH_PARAM_HEADLESS_SERVICE_NAME:-${BENCH_PARAM_CLUSTER_PREFIX}-svc}"
export BENCH_PARAM_EXPECTED_REPLICAS="${BENCH_PARAM_EXPECTED_REPLICAS:-3}"
export BENCH_PARAM_ADMIN_SECRET_NAME="${BENCH_PARAM_ADMIN_SECRET_NAME:-admin-user-password}"
export BENCH_PARAM_APP_SECRET_NAME="${BENCH_PARAM_APP_SECRET_NAME:-app-user-password}"
export BENCH_PARAM_KEYFILE_SECRET_NAME="${BENCH_PARAM_KEYFILE_SECRET_NAME:-mongo-keyfile}"
export BENCH_PARAM_MONGO_IMAGE="${BENCH_PARAM_MONGO_IMAGE:-mongo:6.0.5}"
export BENCH_PARAM_REPLICA_SET_NAME="${BENCH_PARAM_REPLICA_SET_NAME:-${BENCH_PARAM_CLUSTER_PREFIX}}"

static_solver_run_vendored_shell "vendor/import-improve-resources/scripts/resource-solvers/solvers/solve_mongo_deploy.sh"
