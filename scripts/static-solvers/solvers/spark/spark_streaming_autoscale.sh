#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: spark/spark_streaming_autoscale
# Strategy: native_shell
# Notes: Drives the worker deployment through the expected traffic phases and
# waits for the metrics-server to record multiple scaling events.

static_solver_export_namespace_if_unset "spark-streaming"

ns="${BENCH_NAMESPACE}"
worker_deployment="spark-worker"

wait_for_scaling_events() {
  local minimum_events="${1:?minimum event count is required}"
  local timeout_sec="${2:-90}"
  local deadline=$((SECONDS + timeout_sec))
  local count="0"

  while (( SECONDS < deadline )); do
    count="$(
      kubectl -n "${ns}" logs -l app=metrics-server --tail=-1 2>/dev/null | \
        grep -c 'SCALING EVENT' || true
    )"
    if (( count >= minimum_events )); then
      static_solver_log "metrics-server recorded ${count} scaling events"
      return 0
    fi
    sleep 5
  done

  static_solver_fail "metrics-server only recorded ${count} scaling event(s), expected at least ${minimum_events}"
}

for target in 10 20 5; do
  kubectl -n "${ns}" scale "deployment/${worker_deployment}" --replicas="${target}"
  kubectl -n "${ns}" rollout status "deployment/${worker_deployment}" --timeout=300s
  sleep 6
done

wait_for_scaling_events 2

static_solver_write_submit "completed manual Spark worker scaling sequence"
