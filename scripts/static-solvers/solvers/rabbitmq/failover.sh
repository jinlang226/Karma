#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: rabbitmq/failover
# Strategy: native_python
# Notes: Waits for the deleted StatefulSet pod to be recreated before attempting
# the cluster rejoin sequence, which makes the solver resilient to the normal
# NotFound gap between pod deletion and replacement.

python3 "${SCRIPT_DIR}/failover.py"
static_solver_write_submit "submitted static solver for rabbitmq/failover"
