#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# Current case: cockroachdb/major-upgrade-finalize
# Strategy: native_shell
# Notes: Finalizes the inherited upgrade target without downgrading patch
# versions and works for both insecure and secure clusters.

static_solver_export_namespace_if_unset "cockroachdb"
static_solver_export_cockroachdb_defaults

ns="${BENCH_NAMESPACE}"
prefix="${BENCH_PARAM_CLUSTER_PREFIX}"
statefulset_image="$(
  kubectl -n "${ns}" get statefulset "${prefix}" \
    -o jsonpath='{.spec.template.spec.containers[0].image}'
)"
live_version="${statefulset_image##*:}"
live_version="${live_version#v}"
configured_version="${BENCH_PARAM_TO_VERSION:-${live_version:-24.1.0}}"
to_version="${configured_version}"

# The standalone case defaults BENCH_PARAM_TO_VERSION to 24.1.0, but chained
# workflows can legally inherit a newer patch in the same major.minor family
# (for example, a prior partitioned-update to 24.1.1). Preserve that live patch
# instead of silently downgrading back to the case default during finalize.
if [[ -n "${live_version}" ]]; then
  live_family="$(printf '%s' "${live_version}" | cut -d. -f1,2)"
  configured_family="$(printf '%s' "${configured_version}" | cut -d. -f1,2)"
  if [[ "${live_family}" = "${configured_family}" ]]; then
    to_version="${live_version}"
  fi
fi

target_family="$(printf '%s' "${to_version}" | cut -d. -f1,2)"
target_image="cockroachdb/cockroach:v${to_version}"
conn_flag=(--insecure)

[[ -n "${to_version}" ]] || static_solver_fail "failed to resolve target CockroachDB version"
static_solver_log \
  "major-upgrade-finalize live=${live_version:-unknown} configured=${configured_version} target=${to_version}"

if kubectl -n "${ns}" exec "${prefix}-0" -- ls /cockroach/cockroach-certs/ca.crt >/dev/null 2>&1; then
  conn_flag=(--certs-dir=/cockroach/cockroach-certs)
fi

kubectl -n "${ns}" set image "statefulset/${prefix}" "db=${target_image}"
kubectl -n "${ns}" rollout status "statefulset/${prefix}" --timeout=1200s

for _ in $(seq 1 120); do
  pod_images="$(
    kubectl -n "${ns}" get pods -l app.kubernetes.io/name=cockroachdb \
      -o jsonpath='{.items[*].spec.containers[0].image}'
  )"
  pod_count="$(printf '%s\n' "${pod_images}" | wc -w | tr -d ' ')"
  updated_count="$(
    printf '%s\n' "${pod_images}" | tr ' ' '\n' | grep -cx "${target_image}" || true
  )"
  if [[ "${pod_count}" -gt 0 && "${pod_count}" = "${updated_count}" ]]; then
    break
  fi
  sleep 5
done

pod_images="$(
  kubectl -n "${ns}" get pods -l app.kubernetes.io/name=cockroachdb \
    -o jsonpath='{.items[*].spec.containers[0].image}'
)"
pod_count="$(printf '%s\n' "${pod_images}" | wc -w | tr -d ' ')"
updated_count="$(
  printf '%s\n' "${pod_images}" | tr ' ' '\n' | grep -cx "${target_image}" || true
)"
if [[ "${pod_count}" -eq 0 || "${pod_count}" != "${updated_count}" ]]; then
  static_solver_fail "CockroachDB pods did not converge to ${target_image}"
fi

kubectl -n "${ns}" exec "${prefix}-0" -- \
  ./cockroach sql "${conn_flag[@]}" -e \
  "RESET CLUSTER SETTING cluster.preserve_downgrade_option;"

for _ in $(seq 1 120); do
  version="$(
    kubectl -n "${ns}" exec "${prefix}-0" -- \
      ./cockroach sql "${conn_flag[@]}" --format=tsv \
      -e 'SHOW CLUSTER SETTING version;' | tail -n1 | tr -d '\r'
  )"
  if [[ "${version}" = "${target_family}" || "${version}" = "${to_version}" ]]; then
    static_solver_write_submit "upgraded and finalized CockroachDB"
    exit 0
  fi
  sleep 5
done

static_solver_fail "CockroachDB cluster version did not finalize to ${target_family}"
