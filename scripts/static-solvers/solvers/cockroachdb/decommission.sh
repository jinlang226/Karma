#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/decommission
# Strategy: native_shell
# Notes: Restores the case's expected source topology when a workflow inherited
# fewer nodes, decommissions every high ordinal down to the target, and works
# for both insecure and inherited secure clusters.

static_solver_export_namespace_if_unset "cockroachdb"

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX:-crdb-cluster}"
sql_host="${prefix}-0.${prefix}.${ns}.svc.cluster.local"
target_nodes="${BENCH_PARAM_TARGET_NODES:-${BENCH_PARAM_EXPECTED_NODES:-${BENCH_PARAM_TO_REPLICA_COUNT:-3}}}"
source_nodes="${BENCH_PARAM_SOURCE_NODES:-${BENCH_PARAM_SEED_NODE_COUNT:-${BENCH_PARAM_FROM_REPLICA_COUNT:-5}}}"
seed_table="${BENCH_PARAM_SEED_TABLE_NAME:-bench.decom_data}"
conn_flag=(--insecure)

[[ "${target_nodes}" =~ ^[0-9]+$ ]] || static_solver_fail "target node count must be numeric"
[[ "${source_nodes}" =~ ^[0-9]+$ ]] || static_solver_fail "source node count must be numeric"
(( target_nodes > 0 )) || static_solver_fail "target node count must be positive"
(( source_nodes >= target_nodes )) || static_solver_fail "source node count must be >= target node count"

if kubectl -n "${ns}" exec "${prefix}-0" -- ls /cockroach/cockroach-certs/ca.crt >/dev/null 2>&1; then
  conn_flag=(--certs-dir=/cockroach/cockroach-certs)
fi

wait_for_cluster() {
  local expected_replicas="${1:?expected replica count is required}"
  local timeout_sec="${2:-900}"
  local deadline=$((SECONDS + timeout_sec))
  local spec_replicas="0"
  local ready_replicas="0"
  local live_nodes="0"
  local node_status=""

  while (( SECONDS < deadline )); do
    read -r spec_replicas ready_replicas <<< "$(
      kubectl -n "${ns}" get statefulset "${prefix}" \
        -o jsonpath='{.spec.replicas} {.status.readyReplicas}' 2>/dev/null || echo "0 0"
    )"
    spec_replicas="${spec_replicas:-0}"
    ready_replicas="${ready_replicas:-0}"

    if kubectl -n "${ns}" exec "${prefix}-0" -- \
      ./cockroach sql "${conn_flag[@]}" --host="${sql_host}" -e 'SELECT 1;' >/dev/null 2>&1; then
      node_status="$(
        kubectl -n "${ns}" exec "${prefix}-0" -- \
          ./cockroach node status "${conn_flag[@]}" --format=tsv 2>/dev/null || true
      )"
      live_nodes="$(
        printf '%s\n' "${node_status}" | awk -F'\t' '
          NR == 1 {
            for (i = 1; i <= NF; i++) {
              if ($i == "is_live") {
                col = i
              }
            }
            next
          }
          col && tolower($col) == "true" {
            count++
          }
          END {
            print count + 0
          }
        '
      )"
      if [[ "${spec_replicas}" = "${expected_replicas}" &&
        "${ready_replicas}" = "${expected_replicas}" &&
        "${live_nodes}" = "${expected_replicas}" ]]; then
        static_solver_log \
          "cluster ready: spec=${spec_replicas} ready=${ready_replicas} live=${live_nodes}"
        return 0
      fi
    fi

    sleep 5
  done

  static_solver_fail \
    "cluster did not stabilize at ${expected_replicas} nodes (last: spec=${spec_replicas} ready=${ready_replicas} live=${live_nodes})"
}

decommission_status_for_pod() {
  local ordinal="${1:?ordinal is required}"
  kubectl -n "${ns}" exec "${prefix}-0" -- \
    ./cockroach node status "${conn_flag[@]}" --decommission --format=tsv 2>/dev/null | \
    awk -F'\t' -v pod="${prefix}-${ordinal}" '
      NR == 1 {
        for (i = 1; i <= NF; i++) {
          if ($i == "address") {
            addr = i
          } else if ($i == "membership") {
            membership = i
          } else if ($i == "is_decommissioned") {
            decom = i
          } else if ($i == "is_decommissioning") {
            decom_ing = i
          } else if ($i == "is_live") {
            live = i
          }
        }
        next
      }
      addr && index($addr, pod) {
        if (membership) {
          print tolower($membership)
        } else if (decom && tolower($decom) == "true") {
          print "decommissioned"
        } else if (decom_ing && tolower($decom_ing) == "true") {
          print "decommissioning"
        } else if (live && tolower($live) == "true") {
          print "active"
        } else {
          print "unknown"
        }
        exit
      }
    '
}

resolve_node_id() {
  local ordinal="${1:?ordinal is required}"
  kubectl -n "${ns}" exec "${prefix}-0" -- \
    ./cockroach node status "${conn_flag[@]}" --decommission --format=tsv 2>/dev/null | \
    awk -F'\t' -v pod="${prefix}-${ordinal}" '
      NR == 1 {
        for (i = 1; i <= NF; i++) {
          if ($i == "address") {
            addr = i
          }
        }
        next
      }
      addr && index($addr, pod) {
        print $1
        exit
      }
    '
}

wait_for_decommissioned_targets() {
  local deadline=$((SECONDS + 420))
  local live_active="0"
  local state=""
  local ok="0"
  local ordinal=""

  while (( SECONDS < deadline )); do
    live_active="$(
      kubectl -n "${ns}" exec "${prefix}-0" -- \
        ./cockroach node status "${conn_flag[@]}" --decommission --format=tsv 2>/dev/null | \
        awk -F'\t' '
          NR == 1 {
            for (i = 1; i <= NF; i++) {
              if ($i == "is_live") {
                live = i
              } else if ($i == "membership") {
                membership = i
              } else if ($i == "is_decommissioned") {
                decom = i
              } else if ($i == "is_decommissioning") {
                decom_ing = i
              }
            }
            next
          }
          {
            is_live = live && tolower($live) == "true"
            is_decommissioned = 0
            if (membership) {
              is_decommissioned = tolower($membership) == "decommissioned"
            } else if (decom) {
              is_decommissioned = tolower($decom) == "true"
            } else if (decom_ing) {
              is_decommissioned = tolower($decom_ing) == "true"
            }
            if (is_live && !is_decommissioned) {
              count++
            }
          }
          END {
            print count + 0
          }
        '
    )"

    ok="1"
    for ordinal in $(seq "${target_nodes}" $((source_nodes - 1))); do
      state="$(decommission_status_for_pod "${ordinal}")"
      if [[ "${state}" != "decommissioned" ]]; then
        ok="0"
        break
      fi
    done

    if [[ "${ok}" = "1" && "${live_active}" = "${target_nodes}" ]]; then
      return 0
    fi

    sleep 5
  done

  static_solver_fail "decommissioned nodes did not settle to the expected final state"
}

static_solver_log \
  "decommission source_nodes=${source_nodes} target_nodes=${target_nodes} mode=${conn_flag[*]}"

current_replicas="$(
  kubectl -n "${ns}" get statefulset "${prefix}" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "0"
)"
current_replicas="${current_replicas:-0}"
if (( current_replicas < source_nodes )); then
  static_solver_log "scaling ${prefix} from ${current_replicas} to ${source_nodes} before decommission"
  kubectl -n "${ns}" scale "statefulset/${prefix}" --replicas="${source_nodes}"
  kubectl -n "${ns}" rollout status "statefulset/${prefix}" --timeout=900s
fi

wait_for_cluster "${source_nodes}" 900

for ordinal in $(seq "${target_nodes}" $((source_nodes - 1))); do
  state="$(decommission_status_for_pod "${ordinal}")"
  if [[ "${state}" = "decommissioned" ]]; then
    static_solver_log "${prefix}-${ordinal} already decommissioned"
    continue
  fi

  node_id="$(resolve_node_id "${ordinal}")"
  [[ -n "${node_id}" ]] || static_solver_fail "failed to resolve node id for ${prefix}-${ordinal}"
  static_solver_log "decommissioning ${prefix}-${ordinal} (node id ${node_id})"
  kubectl -n "${ns}" exec "${prefix}-0" -- \
    ./cockroach node decommission "${node_id}" "${conn_flag[@]}" --wait=all
done

kubectl -n "${ns}" scale "statefulset/${prefix}" --replicas="${target_nodes}"
kubectl -n "${ns}" rollout status "statefulset/${prefix}" --timeout=900s

wait_for_cluster "${target_nodes}" 900
wait_for_decommissioned_targets

seed_rows="$(
  kubectl -n "${ns}" exec "${prefix}-0" -- \
    ./cockroach sql "${conn_flag[@]}" --host="${sql_host}" --format=tsv \
    -e "SELECT count(*) FROM ${seed_table};" | tail -n1 | tr -d '\r'
)"
[[ "${seed_rows}" =~ ^[0-9]+$ ]] || static_solver_fail "failed to read seeded table row count"
(( seed_rows >= 3 )) || static_solver_fail "expected at least 3 seeded rows, got ${seed_rows}"

static_solver_write_submit "decommissioned CockroachDB nodes"
